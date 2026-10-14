# Invoice Extractor API — Project Documentation

## What This Project Is

A FastAPI service that accepts an invoice image or PDF and returns structured data (vendor, amounts, line items, dates) extracted by Azure's AI model.

The point of this project is **not** the invoice feature itself — it's a vehicle to learn and demonstrate:
- Realistic FastAPI project structure (not toy CRUD)
- Azure AI/PaaS integration
- Redis caching + async background processing (Step 2) ✅
- Microsoft Entra authentication (Step 3)
- Containerisation (Step 4) ✅
- Terraform infrastructure (Step 5)
- Git branching strategy (Step 6)
- Full DevSecOps pipeline on Azure DevOps (Step 7)

---

## Why Invoice Extraction?

Invoice processing is a genuine enterprise problem: companies receive thousands of invoices monthly and historically needed people to manually key in vendor name, amount, dates into an ERP. Automating this is a well-understood, high-value use case — which makes it a credible demo rather than a "Books API".

It also naturally requires:
- **File upload** (not just JSON)
- **An external AI service** (Azure Document Intelligence)
- **Background processing** — OCR takes 3–8 seconds, you don't hold an HTTP connection open that long
- **Caching** — the same invoice PDF shouldn't be re-processed; hash the bytes, cache the result

---

## Architecture & Project Structure

```
app/
├── main.py          → App entry point only. Registers routers, middleware.
├── config.py        → All environment/config loading in one place.
├── models/
│   └── invoice.py   → Data shapes (Pydantic). What fields exist, their types.
├── routers/
│   └── invoices.py  → HTTP layer only. Validates input, calls service, returns response.
└── services/
    ├── extractor.py → Business logic. All Azure SDK calls live here.
    └── cache.py     → All Redis operations live here. Two key patterns: result cache + task status.
```

### Why this separation matters

The router should not know *how* extraction works. The service should not know it's being called over HTTP. This matters because:

- You can swap the extractor service (e.g. replace Azure with Google Vision) without touching the router
- You can unit-test the service without spinning up a FastAPI server
- The router handles HTTP concerns (status codes, validation, auth); the service handles business concerns (what to call, how to interpret the response)

This is the same pattern used in production codebases — "thin controllers, fat services."

---

## Key Concepts & Decisions

### 1. Azure Document Intelligence — What It Actually Is

Azure Document Intelligence (formerly Form Recognizer) is an Azure AI service that was trained on millions of real documents. It understands document *layout* — not just text.

The critical distinction vs. a general LLM:

| | Document Intelligence | LLM (GPT-4, etc.) |
|---|---|---|
| Understands pixel layout | ✅ Yes — knows tables, boxes, columns | ⚠️ No — only sees text |
| Output | Structured JSON, always consistent | Free-form text, needs parsing |
| Cost | ~$1.50 / 1,000 pages | ~$15–60 / 1,000 pages |
| Confidence scores | ✅ Per-field confidence | ❌ No per-field confidence |
| Hallucination risk | Very low (extracts, doesn't generate) | Present |

We use the **`prebuilt-invoice`** model — a model Azure already trained and hosts. We don't train anything ourselves. "Prebuilt" means Azure's team trained it; we just call it.

Other prebuilt models exist: `prebuilt-receipt`, `prebuilt-idDocument`, `prebuilt-contract`. For custom document types, you'd train a custom model in Azure's portal.

---

### 2. The Poller Pattern (begin → result)

```python
poller = client.begin_analyze_document(...)
result = poller.result()
```

Azure processes documents asynchronously on their infrastructure. When you call `begin_analyze_document`, Azure starts processing and immediately returns a job handle (the poller). The SDK then polls Azure's status endpoint every ~1 second under the hood. Calling `.result()` blocks your thread until Azure is done.

This blocking call is what we move into a background task in Step 2 — so the HTTP response doesn't wait for it.

---

### 3. Why Currency Fields Are Objects, Not Floats

Azure returns monetary values as `CurrencyValue(amount=1250.0, currencySymbol='$', currencyCode='USD')` — not as plain floats.

Why? Because `1250` is ambiguous without currency context. The object preserves the full financial information.

This is why `_safe_float()` exists — it handles both `CurrencyValue` objects and plain numeric fields gracefully without crashing on unexpected types.

---

### 4. Confidence Scores

Every field Azure extracts has a `confidence` value (0.0 to 1.0). A high-quality, clearly-printed invoice might give `VendorName: 0.98`. A blurry photo or unusual layout might give `InvoiceTotal: 0.61`.

We average all field confidences to produce an overall document confidence score. This is returned to the client so they can decide what to do:
- `confidence > 0.9` → auto-approve, post to ERP
- `confidence 0.6–0.9` → flag for human review
- `confidence < 0.6` → reject, request rescan

This human-in-the-loop pattern based on confidence is standard in production document processing pipelines.

---

### 5. FastAPI Dependency Injection (`Depends`)

```python
async def extract_invoice(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),  # ← this
):
```

`Depends` tells FastAPI: "before calling this function, call `get_settings()` and inject its return value as the `settings` parameter."

Why not just import settings at the top of the file?
- **Testability**: In tests, you can override `get_settings` to return a mock config with test credentials — without touching any module-level state
- **Lifecycle control**: `Depends` can also handle database connections, auth token validation, rate limiting — anything that should run per-request

`@lru_cache` on `get_settings` means the `.env` file is only read once regardless of how many requests come in — the same `Settings` object is reused.

---

### 6. Why `content_type` is Passed to Azure

```python
poller = client.begin_analyze_document(
    model_id="prebuilt-invoice",
    body=io.BytesIO(file_bytes),
    content_type=content_type,   # "image/jpeg" or "application/pdf"
)
```

Azure needs to know the format to decode the bytes correctly. A JPEG and a PDF are completely different binary formats. Without `content_type`, Azure can't parse the file. We get this from the `UploadFile.content_type` that FastAPI extracts from the multipart form's `Content-Type` header.

---

### 7. Redis Caching + Background Tasks (Step 2)

#### The problem with synchronous extraction

Every call to Azure Document Intelligence takes 3–8 seconds and costs money. If the same invoice is uploaded twice, we'd pay twice and wait twice. Holding an HTTP connection open for 8 seconds is also bad practice — mobile clients time out, load balancers drop connections, and it limits throughput.

#### The solution: async task + Redis cache

```
POST /invoices/extract  →  returns task_id in ~50ms  (HTTP 202 Accepted)
                                  ↓
                     background task runs Azure call
                                  ↓
                     result stored in Redis under two keys

GET /invoices/status/{task_id}  →  client polls until status="done"
```

#### Two Redis key patterns

| Key | Value | TTL | Purpose |
|-----|-------|-----|---------|
| `invoice:{sha256_hash}` | Extraction result JSON | 24 hours | Same file → instant result, zero Azure cost |
| `task:{uuid}` | Status + result JSON | 1 hour | Polling endpoint reads this |

**Why hash the file content (not the filename)?**
The same invoice could be uploaded as `invoice.pdf`, `scan.pdf`, `copy.pdf`. The filename means nothing — the content is what matters. SHA-256 of the bytes gives a unique fingerprint of the actual content.

#### Why Redis and not a SQL database for caching?

- Redis is **in-memory** → microsecond reads vs. milliseconds for SQL
- Redis has **native TTL** — keys auto-expire, no cleanup job or cron needed
- For temporary/cache data, a database adds unnecessary complexity (schema, migrations, connection pooling, disk I/O)
- Redis is the industry standard for this pattern (caching + task result storage)

#### Why `asyncio.to_thread()` inside the background task?

The Azure SDK is a synchronous (blocking) library. Calling it directly inside an `async def` function would block the entire event loop — no other requests could be served during the 3–8 second Azure call.

`asyncio.to_thread()` runs the blocking function in a separate thread from the thread pool, keeping the event loop free to handle other requests concurrently.

#### HTTP 202 Accepted vs 200 OK

`202 Accepted` is the semantically correct HTTP status for "we received your request, processing has started, come back for the result." `200 OK` would imply the work is done, which it isn't. This distinction matters in REST API design and is a common interview topic.

---

### 8. Do We Need a User Database? (Q&A)

**Short answer: No — and for good reason.**

**What Microsoft Entra ID (Step 3) replaces:**

In a traditional app without Entra, you'd build:
- User registration endpoint
- Password hashing (bcrypt)
- Login endpoint + JWT generation
- Token refresh logic
- Password reset flow
- A `users` table in a database
- Session management

Entra ID handles **all of this** for you. It's Azure's identity platform. Users log in through Microsoft's infrastructure; your API just validates the token they present. You never store passwords, never issue tokens, never manage sessions.

**In real-world enterprise systems, is a DB ever used alongside Entra?**

Yes — but for **application data**, not authentication data. The distinction:

| Data | Where it lives | Why |
|------|---------------|-----|
| Who the user is, password, login | Entra ID | Not our problem to manage |
| User's app preferences, settings | Database (Postgres, etc.) | Application-specific data |
| Which invoices belong to which user | Database | Business data |
| Audit log (who processed what invoice) | Database or Log Analytics | Compliance |
| Extracted invoice results (temporary) | Redis | Cache — doesn't need to persist |

**For this project specifically:**

We skip the database entirely because:
1. Entra handles identity
2. Redis handles temporary results
3. There's no business requirement to store invoices long-term — the use case is extract and return, not store and manage

In a real production system, you'd likely add Postgres later for things like: invoice history per organisation, audit trails, storing enriched results permanently. But that's application scope, not authentication scope — and it's not needed to demonstrate any of the concepts in this project.

**The interview-ready answer:**
> "We use Microsoft Entra ID for authentication — it replaces everything a user database would handle for identity. For application data persistence, we'd introduce a database (Postgres on Azure) if the use case required storing results long-term, but for this project the combination of Redis (temporary cache) and Entra (identity) covers all requirements without the overhead of a relational database."

---

### 9. Microsoft Entra ID Authentication (Step 3)

#### What Entra replaces

Without Entra, you'd build: user registration, password hashing, login endpoints, JWT generation, token refresh, password reset, a users table. Entra handles all of that. Your API only validates the token it receives — it never sees a password.

---

#### The OAuth2 Authorization Code Flow — browser vs back-channel

This is the most confusing part of OAuth2. There are **two completely separate channels** and it's important to know which is which.

---

**Phase 1 — Front-channel (browser redirects, URL is visible)**

```
You (browser)                            Entra (Microsoft)
      │                                        │
      │─ Click "Authorize" in Swagger UI ─────▶│
      │  URL: /oauth2/v2.0/authorize           │
      │       ?client_id=8d9daf82-...          │
      │       &scope=invoices.extract          │
      │       &redirect_uri=localhost/callback │
      │       &response_type=code              │
      │                                        │
      │◀─ Microsoft login page shown ──────────│
      │                                        │
      │─ You type your Microsoft password ────▶│
      │                                        │
      │◀─ Browser redirected back ─────────────│
         URL: localhost:8000/docs/oauth2-redirect
              ?code=abc123xyz   ← short-lived, one-time code
```

The `code` in the URL is **not a token**. It is a one-time-use disposable code valid for about 10 seconds. Your password never left Microsoft — Entra just confirmed "yes, this person authenticated" and sent back a code.

---

**Phase 2 — Back-channel (direct HTTP call, browser never sees this)**

```
Swagger UI (JavaScript)                  Entra
      │                                     │
      │─ POST /oauth2/v2.0/token ──────────▶│
      │  Body (form data):                  │
      │    grant_type=authorization_code    │
      │    code=abc123xyz                   │
      │    client_id=8d9daf82-...           │
      │    redirect_uri=localhost/callback  │
      │    code_verifier=... (PKCE)         │
      │                                     │
      │◀─ Access Token (JWT) ───────────────│
           eyJhbGciOiJSUzI1NiIs...
```

This is a background JavaScript `fetch()` call — no redirect, no URL change. The access token arrives in the response body. Swagger UI stores it in memory (lost when you close the tab). This exchange is called the **back-channel** because it bypasses the browser's URL bar entirely.

**Why split into two phases?**
The code in the URL is visible in browser history, server logs, and referrer headers. The actual token must never appear in a URL — only over a direct HTTPS POST. The two-phase split ensures the token travels only over encrypted back-channel.

---

**Client Secret vs PKCE — how Entra knows the code exchange is legitimate**

You might wonder: *anyone who intercepts the code in the URL could try to exchange it for a token — how does Entra prevent that?*

There are two answers depending on the type of client:

**Confidential client (server-side app — can keep a secret):**
```
POST /oauth2/v2.0/token
  code=abc123xyz
  client_id=8d9daf82-...
  client_secret=awh8Q~Pk...   ← proves "I am the real registered app"
```
Entra verifies the secret matches. Anyone who stole the code but doesn't have the secret can't use it.

**Public client (browser app / Swagger UI — cannot keep a secret):**
You can't embed a client secret in JavaScript — anyone can View Source and steal it. Instead, browser-based apps use **PKCE (Proof Key for Code Exchange)**:

```
BEFORE Phase 1:
  Swagger UI generates a random string:
    code_verifier  = "xK9mP2rQs7..."   (kept in memory, never leaves)
    code_challenge = SHA256("xK9mP2rQs7...") = "aB3cD4..."  (the hash)

Phase 1 — sends only the HASH (safe to expose):
  GET /authorize?
    code_challenge=aB3cD4...
    code_challenge_method=S256
  Entra stores: "for this code, expect a verifier that hashes to aB3cD4..."

Phase 2 — sends the ORIGINAL string (never seen before):
  POST /token
    code=abc123xyz
    client_id=8d9daf82-...
    code_verifier=xK9mP2rQs7...    ← NOT the hash — the original
    (no client_secret needed)

  Entra checks: SHA256("xK9mP2rQs7...") == "aB3cD4..."? YES → issues token
```

If an attacker steals the code from the URL, they still can't exchange it — they'd need `code_verifier` which was only ever held in Swagger UI's memory, never exposed.

**In this project:**
- Swagger UI uses PKCE (`usePkceWithAuthorizationCodeGrant: True` in main.py)
- The `AZURE_CLIENT_SECRET` in `.env` is not used for the Swagger UI login flow
- It would be used if a server-side app (e.g. a Python/Node frontend) did the token exchange on behalf of users, or for Client Credentials flow (machine-to-machine, no user login)

---

**Security Deep Dive: What stops someone from mimicking the PKCE flow and requesting an auth code out of the blue? (Q&A)**

A common question in OAuth2 security is: *The `/authorize` endpoint is public and Swagger UI uses PKCE without a client secret — so what stops an attacker or script from mimicking the PKCE flow and requesting an authorization code out of the blue?*

Multiple independent defense layers make this attack impossible:

1. **Authentication & Consent Barrier (Entra ID Login)**
   - The `/authorize` endpoint does **not** return an authorization code automatically.
   - It redirects the browser to Microsoft's login interface. A valid user identity must authenticate (username, password, MFA, Conditional Access policies) and grant consent to the requested scopes (`invoices.extract`).
   - Without valid user credentials and completed authentication, no authorization code is ever generated or issued.

2. **Strict Redirect URI Whitelisting (The Code Only Goes Home)**
   - The authorization code is never returned in an HTTP response body; it is delivered via a `302 Redirect` to the client's `redirect_uri`.
   - Entra ID strictly validates the `redirect_uri` against the pre-registered URLs configured in the Azure App Registration (e.g., `http://localhost:8000/docs/oauth2-redirect.html`).
   - If an attacker specifies `redirect_uri=https://attacker-controlled-server.com/steal`, Entra ID immediately rejects the request with `AADSTS50011` and refuses to issue the code.

3. **Cryptographic Protection Against Code Interception (The Core of PKCE)**
   - What if an attacker intercepts the short-lived authorization code in transit or on the local client machine?
     - The legitimate client generated a cryptographically random secret string (`code_verifier`) kept solely in memory, and sent only its SHA-256 hash (`code_challenge`) during the initial authorization request.
     - To exchange the stolen code at `/token`, the attacker must supply the original `code_verifier`.
     - Because SHA-256 is a one-way cryptographic hash function, an attacker cannot reverse-engineer the `code_challenge` to deduce the `code_verifier`. Without the verifier, Entra ID rejects the token exchange.
   - In addition, authorization codes are single-use and expire within minutes (typically 5–10 minutes).

4. **Identity Binding & Role-Based Access Control**
   - If an attacker runs their own PKCE flow using their own valid Microsoft account, they will only receive a token bound to *their* identity (`oid`, `upn`, `roles`).
   - They cannot forge or claim another user's identity. The backend API validates the token claims on every single request.

---

**Phase 3 — Using the token (who puts what in headers)**


```
Swagger UI / your app / Postman          FastAPI (your API)
      │                                        │
      │─ POST /invoices/extract ──────────────▶│
      │  Headers:                             │
      │    Authorization: Bearer eyJhbGci... │  ← CLIENT adds this
      │  Body: [invoice file bytes]           │
      │                                        │
      │◀─ 202 Accepted + task_id ─────────────│
```

**Who puts the token in the `Authorization` header?**
The **client** (Swagger UI, frontend app, Postman, curl) — always. The API never sets this header. The flow is:

1. Client receives the access token from Entra (Phase 2)
2. Client stores it (Swagger UI stores in memory; a real app might store in a secure cookie or sessionStorage)
3. On every API request, the client attaches: `Authorization: Bearer <token>`
4. FastAPI reads this header

`Bearer` is just a prefix meaning "whoever holds (bears) this token has access." No additional proof is needed — possession of the token is enough. This is why HTTPS is essential: a stolen Bearer token = stolen identity until it expires.

---

#### What `OAuth2AuthorizationCodeBearer` actually does in FastAPI

It sounds complex but it just reads the header:

```python
# Simplified — what it does internally
def __call__(self, request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    # auth_header = "Bearer eyJhbGciOiJSUzI1NiIs..."

    scheme, token = auth_header.split(" ", 1)
    # scheme = "Bearer"
    # token  = "eyJhbGciOiJSUzI1NiIs..."

    return token  # this is what get_current_user receives
```

It also configures Swagger UI with the Entra URLs (authorizationUrl, tokenUrl) so the "Authorize" button knows where to send the user.

---

#### How JWT validation works (inside `get_current_user`)

A JWT is three base64-encoded JSON parts separated by dots:

```
eyJhbGciOiJSUzI1NiIsImtpZCI6ImFiYzEyMyJ9   ← HEADER
.eyJvaWQiOiJ1c2VyLWd1aWQiLCJ1cG4iOiJ5b3VAY29tcGFueS5jb20ifQ==   ← PAYLOAD
.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c   ← SIGNATURE
```

**HEADER** contains metadata: `{ "alg": "RS256", "kid": "abc123" }`
- `kid` = which of Entra's public keys signed this token

**PAYLOAD** contains claims: `{ "oid": "user-guid", "upn": "you@company.com", "iss": "https://login.microsoftonline.com/{tenant}/v2.0", "aud": "api://8d9daf82-...", "exp": 1727934600, "roles": [] }`

**SIGNATURE** = cryptographic proof that Entra (and only Entra) created this token

**Validation steps in `get_current_user`:**

```
1. Read "kid" from HEADER
        ↓
2. Fetch Entra's public key matching that kid
   from: login.microsoftonline.com/{tenant}/discovery/v2.0/keys
   (cached in memory — not fetched every request)
        ↓
3. Verify SIGNATURE using the public key
   → proves Entra's private key signed this; nobody else could have
        ↓
4. Check "iss" = our tenant's issuer URL
   → prevents tokens from other Entra tenants
        ↓
5. Check "aud" = our API's Application ID URI (api://8d9daf82-...)
   → prevents tokens issued for other apps being used here
        ↓
6. Check "exp" > current time
   → token not expired (tokens typically last 1 hour)
        ↓
7. Extract "oid", "upn", "name", "roles" → build CurrentUser
```

**Why public key cryptography (RS256)?**
Entra signs tokens with its **private key** (which never leaves Microsoft). It publishes the matching **public key** at the JWKS URL. We use the public key to verify the signature — but cannot forge new tokens (that requires the private key). This means we can validate tokens completely offline after fetching the keys once.

**Token Error Handling & HTTP 401 Responses:**
- `jwt.ExpiredSignatureError`: The token's expiration timestamp (`exp`) has passed. Handled specifically to return HTTP 401 with detail `"Token has expired."` and the `WWW-Authenticate: Bearer` response header, signaling to the client that it should re-authenticate or refresh.
- `jwt.InvalidTokenError`: Catches all other cryptographic and structural validation failures (invalid signature, wrong issuer `iss`, wrong audience `aud`, malformed token) and returns HTTP 401 Unauthorized.

---

#### App Roles (future — Invoice.Reader, Invoice.Contributor)

Roles are defined in the App Registration → "App roles" section. Once defined and assigned to users:
- The token's `roles` claim contains: `["Invoice.Contributor"]`
- `CurrentUser.roles` is already wired to read this field
- We'll add a `require_role("Invoice.Contributor")` dependency that checks this list

```python
# Future: role enforcement dependency
def require_role(role: str):
    def checker(user: CurrentUser = Depends(get_current_user)):
        if role not in user.roles:
            raise HTTPException(403, "Insufficient role")
        return user
    return checker

# Usage
@router.post("/extract")
async def extract(user = Depends(require_role("Invoice.Contributor"))):
    ...
```

---



## Running Locally

### With Docker (recommended — no local installs needed)

```bash
# Copy and fill in your Azure credentials + Redis URL
cp .env.example .env

# First run builds the image (~20s), subsequent runs are instant
docker compose up --build -d

# Watch logs
docker compose logs -f api
```

API available at: `http://localhost:8000`
Interactive docs: `http://localhost:8000/docs`

### Inspect Redis directly (optional)

```bash
# Connect to the Redis container
docker exec -it invoice-redis redis-cli

# See all stored keys
KEYS *

# Read a cached result (replace <hash> with actual SHA-256)
GET invoice:<hash>

# Check remaining TTL on a key
TTL task:<uuid>
```

---

## Testing the Async Flow

### Step 1: Submit invoice
```bash
curl -X POST "http://localhost:8000/invoices/extract" \
     -F "file=@invoice.pdf"
```

Response (immediate, ~50ms):
```json
{
  "task_id": "a1b2c3d4-...",
  "status": "processing",
  "message": "Invoice submitted. Poll /invoices/status/{task_id} for the result.",
  "cache_hit": false
}
```

### Step 2: Poll for result
```bash
curl "http://localhost:8000/invoices/status/a1b2c3d4-..."
```

Response when done:
```json
{
  "task_id": "a1b2c3d4-...",
  "status": "done",
  "result": {
    "vendor_name": "amazon.in",
    "total": 3599,
    ...
  }
}
```

### Step 3: Upload same file again → cache hit
```json
{
  "task_id": "...",
  "status": "done",
  "message": "Result retrieved from cache.",
  "cache_hit": true
}
```

No Azure call. Instant. Free.

---

## 10. Infrastructure as Code — Terraform (Step 5)

The `terraform/` directory provisions the entire Azure cloud infrastructure required to host the API in production.

### Provisioned Cloud Resources

| Resource | Terraform Type | SKU / Tier | Purpose |
|---|---|---|---|
| **Resource Group** | `azurerm_resource_group` | — | Logical container for all project assets |
| **App Service Plan & Linux Web App** | `azurerm_service_plan`, `azurerm_linux_web_app` | `F1` / `B1` | Hosts the Docker container pulled from Docker Hub |
| **PostgreSQL Flexible Server** | `azurerm_postgresql_flexible_server` | `B_Standard_B1ms` (Burstable) | Relational persistence for processed invoices |
| **Azure Managed Redis** | `azurerm_managed_redis` | `Balanced_B0` | High-throughput distributed cache & task tracking |
| **Document Intelligence** | `azurerm_cognitive_account` | `F0` (Free tier, 500 pages/mo) | OCR & invoice layout analysis AI engine |

### File Structure & Responsibilities

```
terraform/
├── main.tf                 → AzureRM provider (>= 4.50.0), remote state backend, resource group
├── variables.tf            → Input declarations (environment, region, secrets, Entra IDs)
├── terraform.tfvars.example→ Template for local variable definitions (never commit real tfvars)
├── postgres.tf             → PostgreSQL Flexible Server, database, firewall, SSL config
├── redis.tf                → Azure Managed Redis instance + default_database config
├── document_intelligence.tf→ Azure AI Form Recognizer (F0) resource
├── app_service.tf          → App Service container configuration & auto-injected environment variables
└── outputs.tf              → App URL, Swagger URL, and connection strings
```

### Automatic Secret Wiring
The Web App's `app_settings` in `app_service.tf` wires connection strings and keys directly from the created resources (e.g., `${azurerm_managed_redis.main.hostname}` and `${azurerm_cognitive_account.document_intelligence.primary_access_key}`). This eliminates manual copy-pasting of endpoints and keys across services.

---

### Architectural Note: Monorepo vs. Dedicated Infra Repo

> [!NOTE]
> **Why Terraform is in this repository:**
> The `terraform/` folder is placed here for **educational simplicity** — allowing you to see the application code, Docker definitions, migrations, and cloud infrastructure together in a single repository.

#### Production Best Practice
In enterprise production setups, application code and infrastructure code are separated:

1. **Decoupled Lifecycles**: Application code changes frequently (multiple times a day), whereas foundational infrastructure (VNet, databases, Redis) changes infrequently.
2. **Dedicated Infrastructure Repo**: An independent repository (e.g., `invoice-extractor-infra`) manages the cloud environment, state files, and IAM permissions.
3. **Zero Secrets in Files**: Instead of `terraform.tfvars`, secrets are fetched dynamically from **Azure Key Vault** or injected as environment variables (`TF_VAR_<name>`) from CI/CD secret stores.
4. **OIDC Authentication**: CI/CD pipelines connect to Azure using **Workload Identity Federation (OIDC)** instead of long-lived client secrets.

#### Example: Production CI/CD Pattern (GitHub Actions)

In a dedicated infrastructure pipeline, Terraform runs automatically on pull requests and merges:

```yaml
# .github/workflows/infra-deploy.yml (in a dedicated infra repo)
name: "Terraform Infrastructure Pipeline"

on:
  push:
    branches: [ "main" ]
  pull_request:
    branches: [ "main" ]

permissions:
  id-token: write   # Required for Azure OIDC authentication
  contents: read

jobs:
  terraform:
    name: "Terraform Plan & Apply"
    runs-on: ubuntu-latest

    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Azure Login via OIDC
        uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}

      - name: Setup Terraform
        uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "1.7.5"

      - name: Terraform Init
        run: terraform init
        working-directory: ./terraform

      - name: Terraform Plan
        env:
          TF_VAR_docker_image: ${{ secrets.DOCKER_IMAGE }}
          TF_VAR_docker_registry_username: ${{ secrets.DOCKER_USER }}
          TF_VAR_docker_registry_password: ${{ secrets.DOCKER_PASSWORD }}
          TF_VAR_postgres_admin_password: ${{ secrets.PG_ADMIN_PASSWORD }}
          TF_VAR_azure_tenant_id: ${{ secrets.AZURE_TENANT_ID }}
          TF_VAR_azure_client_id: ${{ secrets.AZURE_APP_CLIENT_ID }}
        run: terraform plan -out=tfplan
        working-directory: ./terraform

      - name: Terraform Apply
        if: github.ref == 'refs/heads/main' && github.event_name == 'push'
        run: terraform apply tfplan
        working-directory: ./terraform
```

---

## 11. Automated Testing & Mocking Architecture (Step 6 / Pytest)

The test suite in `tests/` is designed to be **100% deterministic, offline, and self-contained**. Tests execute in under 2 seconds without requiring:
- A live connection to Microsoft Entra ID.
- A running PostgreSQL database instance.
- A running Redis server.
- Azure Document Intelligence API credits.

```
tests/
├── conftest.py              → Global test fixtures & FastAPI dependency overrides
├── test_health.py           → GET /health sanity check
├── test_invoices.py         → End-to-end /invoices endpoint tests (validation, async 202, cache hit/miss, 403)
├── test_extractor_service.py→ Business logic, currency parsing, and Pydantic models
├── test_auth_service.py     → Entra JWT validation, claims extraction, expired token (401), tenant mismatch
├── test_cache_service.py    → Redis async operations (TTL, cache hit/miss, task status tracking)
└── test_database_service.py → PostgreSQL ORM CRUD functions (create, get, list user invoices)
```

---

### Core Mocking Patterns Explained

### 1. `app.dependency_overrides` — Bypassing Entra ID and PostgreSQL

FastAPI features a first-class dependency injection system. In production, our route handlers declare dependencies using `Depends(...)`:

```python
@router.post("/extract")
async def extract_invoice(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),  # Real Entra JWT verification
    db: AsyncSession = Depends(get_db),                     # Real PostgreSQL session
    settings: Settings = Depends(get_settings),             # Production config
):
```

In unit tests, we use `app.dependency_overrides` inside [tests/conftest.py](file:///d:/Azure/FastAPI-103/tests/conftest.py):

```python
app.dependency_overrides[get_current_user] = lambda: mock_user
app.dependency_overrides[get_db] = lambda: mock_db_session
app.dependency_overrides[get_settings] = lambda: test_settings
```

#### Why this is superior to global monkeypatching:
1. **Zero External Calls**: `get_current_user` is never called. The test directly receives a mock `CurrentUser(object_id="...", upn="developer@company.com")` without needing internet access or real JWTs.
2. **Zero Database Required**: `get_db` yields an in-memory `mock_db_session` instead of connecting to PostgreSQL over TCP.
3. **Clean Teardown**: After the test completes, `app.dependency_overrides.clear()` restores the application state so subsequent tests are not affected.

---

### 2. `AsyncMock` vs. `MagicMock` — Handling Async Coroutines

Python's standard library provides both `MagicMock` and `AsyncMock` (`unittest.mock`). In an async codebase (FastAPI + SQLAlchemy 2.0 Async + asyncpg + redis.asyncio), using the wrong mock causes immediate test failures:

```python
# ❌ INCORRECT:
session = MagicMock()
await session.commit()
# Raises: TypeError: object MagicMock can't be used in 'await' expression

# ✅ CORRECT:
session = AsyncMock()
await session.commit()  # Resolves cleanly!
session.commit.assert_awaited_once()  # Verifies the coroutine was actually awaited
```

In [tests/conftest.py](file:///d:/Azure/FastAPI-103/tests/conftest.py), `mock_db_session` is defined with `AsyncMock` so methods like `await db.commit()`, `await db.rollback()`, and `await db.close()` execute seamlessly.

---

### 3. Asynchronous Function Patching via `@patch`

When testing the router in [tests/test_invoices.py](file:///d:/Azure/FastAPI-103/tests/test_invoices.py), we intercept Redis operations and background task triggers:

```python
@patch("app.routers.invoices.get_cached_result", new_callable=AsyncMock)
@patch("app.routers.invoices.set_task_status", new_callable=AsyncMock)
@patch("app.routers.invoices.create_invoice_record", new_callable=AsyncMock)
def test_extract_cache_miss_accepted(mock_create_db, mock_set_status, mock_get_cached, client):
    # 1. Simulate Redis returning a cache miss
    mock_get_cached.return_value = None

    files = {"file": ("invoice.pdf", b"%PDF-1.4 test invoice content", "application/pdf")}
    response = client.post("/invoices/extract", files=files)

    # 2. Verify HTTP response
    assert response.status_code == 202
    assert response.json()["status"] == "processing"

    # 3. Verify Redis and Database interactions were awaited
    mock_set_status.assert_awaited_once()
    mock_create_db.assert_awaited_once()
```

#### Critical Rules When Using `@patch` & Async Testing:

1. **Patch where the object is looked up, not where it is defined**:
   The router imported the function via `from app.services.cache import get_cached_result`. Therefore, we patch `"app.routers.invoices.get_cached_result"` (the namespace where the router accesses it), not `"app.services.cache.get_cached_result"`.

2. **Decorator order is inverted (The "Onion" Model)**:
   Python applies decorators from bottom to top. The bottom decorator (`create_invoice_record`) becomes the *first* test argument (`mock_create_db`), while the top decorator (`get_cached_result`) becomes the *third* argument (`mock_get_cached`).

3. **When to use `new_callable=AsyncMock` vs. standard `MagicMock` (The Golden Rule)**:
   - If the production code calls the function with `await` (e.g. `await get_task_status(...)`):
     You **must** pass `new_callable=AsyncMock`. Without it, Python tries to `await` a standard `MagicMock` and crashes with:  
     `TypeError: object MagicMock can't be used in 'await' expression`.
   - If the production code calls the function synchronously without `await` (e.g. `jwks_client = _get_jwks_client(settings)` or `jwt.decode(...)`):
     Use standard `@patch(...)` (which defaults to `MagicMock`). Adding `AsyncMock` here would cause a crash because synchronous code cannot use unawaited coroutines.

| Target Function | Real Code Call Site | What `@patch` Requires |
| :--- | :--- | :--- |
| `get_task_status` | `await get_task_status(task_id)` | `@patch(..., new_callable=AsyncMock)` |
| `create_invoice_record` | `await create_invoice_record(...)` | `@patch(..., new_callable=AsyncMock)` |
| `get_invoice_record` | `await get_invoice_record(...)` | `@patch(..., new_callable=AsyncMock)` |
| `_get_jwks_client` | `_get_jwks_client(settings)` (no await) | `@patch(...)` (default `MagicMock`) |
| `jwt.decode` | `jwt.decode(...)` (no await) | `@patch(...)` (default `MagicMock`) |

#### Why a Test Function Is `async def` Even When Its Mocks Are Synchronous
In [tests/test_auth_service.py](file:///d:/Azure/FastAPI-103/tests/test_auth_service.py), both patched functions (`_get_jwks_client` and `jwt.decode`) are synchronous. However, the test function itself is declared:
```python
@pytest.mark.asyncio
async def test_get_current_user_valid_token(mock_jwt_decode, mock_get_jwks):
    ...
    user = await get_current_user(token=..., settings=settings)
```
Because the function under test (`get_current_user`) is a FastAPI `async def` dependency, calling it requires `await`. And in Python, `await` is only legal inside an `async def` function running inside an event loop (`@pytest.mark.asyncio`).

#### Pytest Fixture Auto-Discovery (`conftest.py`) — Why `client` and `mock_user` Need No `import`
In [tests/test_invoices.py](file:///d:/Azure/FastAPI-103/tests/test_invoices.py), neither `client` nor `mock_user` is imported at the top of the file:
- Pytest automatically discovers and loads any file named `conftest.py` in the test directory.
- Any function decorated with `@pytest.fixture` inside `conftest.py` (like `def client` or `def mock_user`) is globally available to all test functions.
- Pytest inspects parameter names: when it sees `def test_...(..., client, mock_user):`, it automatically executes the corresponding fixture in `conftest.py` and injects the resulting objects.
- In `test_get_status_success_from_redis`, `mock_user` is injected specifically to set `mock_record.user_id = mock_user.object_id`, proving the endpoint's user-ownership authorization check succeeds.

---

### 4. Auth Service Mocking & Rationale — Simulating Edge Cases (Expired Tokens, Evil Issuers, Tampered Signatures)

A frequent question when starting with unit testing is:  
*If we mock the Azure network call and mock token decoding, are we just "cheating"? What is the actual motive?*

#### The Motive: Testing Our Defensive Logic
We do **not** need to test whether Microsoft Entra ID works (Microsoft already tested that) or whether PyJWT can decrypt RSA keys (PyJWT maintainers already tested that). 

Instead, we are testing **how OUR API behaves under failure conditions**:
- **What if a token is expired?** Does our API crash with an unhandled `500 Internal Server Error`, or does it catch PyJWT's `ExpiredSignatureError` and return a clean HTTP `401 Unauthorized` with an informative message?
- **What if an attacker supplies a token from an untrusted Azure tenant?** Does our issuer verification reject it?
- **What if a token is tampered with or malformed?** Does our code fail safely?
- **What if the token is valid?** Does our code correctly map Microsoft's claims (`oid`, `preferred_username`, `scp`, `roles`) into our internal `CurrentUser` Pydantic model?

Without mocks, testing an expired token would require waiting 60 minutes for a real Azure token to expire. With mocks, we simulate this scenario deterministically in under **5 milliseconds**:

```python
# tests/test_auth_service.py
@pytest.mark.asyncio
@patch("app.services.auth._get_jwks_client")
@patch("jwt.decode")
async def test_get_current_user_expired_token(mock_jwt_decode, mock_get_jwks):
    """
    MOTIVE: Verify our error handling when a client presents an expired token.
    We tell PyJWT's mock to raise ExpiredSignatureError, and verify that
    our API translates it into an HTTP 401 with 'Token has expired'.
    """
    mock_get_jwks.return_value = MagicMock()
    
    # Simulate PyJWT throwing an expiration error
    mock_jwt_decode.side_effect = jwt.ExpiredSignatureError("Signature has expired")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(settings=settings, token="expired.fake.token")

    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()
```

#### Why Wrap with `with pytest.raises(HTTPException) as exc_info` vs. Calling Directly?

When testing error conditions (e.g. an expired token), a common question is: *Why do we wrap the function inside `with pytest.raises(...)` instead of calling it directly?*

```python
# ❌ IF CALLED DIRECTLY:
user = await get_current_user(token="expired.token", settings=settings)
```

1. **Calling directly crashes the test:** An expired token causes `get_current_user` to raise `HTTPException(401)`. In standard Python, an unhandled exception immediately halts execution. Pytest considers this an unexpected crash and marks the test as **FAILED (RED)**.
2. **`pytest.raises` acts as an intelligent assertion:**
   - It tells pytest: *"We EXPECT the code inside this block to raise an `HTTPException`."*
   - If `HTTPException` is raised: Pytest intercepts it safely, prevents a crash, and lets the test continue.
   - If NO exception is raised (e.g., a bug in our auth allowed an expired token through!): Pytest immediately **FAILS** the test with: `Failed: DID NOT RAISE <class 'fastapi.exceptions.HTTPException'>`.
3. **What `as exc_info` provides:**
   It captures the thrown exception object into `exc_info.value` so you can verify the status code and message:
   ```python
   # Verify it gave 401 Unauthorized (and not a 500 server crash):
   assert exc_info.value.status_code == 401
   # Verify the user-facing message informs the client their token expired:
   assert "expired" in exc_info.value.detail.lower()
   ```

#### Why the Decorator Arguments Are Inverted (The "Onion" Model)
Notice the order of `@patch` vs. test function parameters in [tests/test_auth_service.py](file:///d:/Azure/FastAPI-103/tests/test_auth_service.py):

```python
@patch("app.services.auth._get_jwks_client")  # 2. Outer decorator  --> 2nd parameter (mock_get_jwks)
@patch("jwt.decode")                          # 1. Inner decorator  --> 1st parameter (mock_jwt_decode)
async def test_get_current_user_valid_token(mock_jwt_decode, mock_get_jwks):
```

In Python, stacked decorators evaluate **from the bottom up** (inside-out, like layers of an onion):
1. `@patch("jwt.decode")` wraps the test function first, injecting its mock as parameter #1 (`mock_jwt_decode`).
2. `@patch("app.services.auth._get_jwks_client")` wraps around that result, injecting its mock as parameter #2 (`mock_get_jwks`).
> **Rule to remember:** The decorator closest to `def` is always the first parameter in your function.

#### How `mock_jwks` Works (The MagicMock Chain)
In production [app/services/auth.py](file:///d:/Azure/FastAPI-103/app/services/auth.py), the code executes:
```python
jwks_client = _get_jwks_client(settings)
signing_key = jwks_client.get_signing_key_from_jwt(token)
payload = jwt.decode(token, signing_key.key, ...)
```

In the test, we assign:
```python
mock_jwks = MagicMock()
mock_get_jwks.return_value = mock_jwks
```
Because `mock_jwks` is a `MagicMock`:
1. `_get_jwks_client(settings)` returns `mock_jwks` (no network call to Azure's JWKS endpoint).
2. Calling `jwks_client.get_signing_key_from_jwt(token)` automatically returns *another* new `MagicMock` (`signing_key`).
3. Accessing `signing_key.key` also yields a dummy mock object, preventing `AttributeError`.
4. `jwt.decode` is intercepted by `@patch("jwt.decode")`, completely bypassing RSA verification and directly returning our simulated payload or exception.

---

### 5. Database Service Mocking — Real Objects vs. MagicMock & Chained Queries

In [tests/test_database_service.py](file:///d:/Azure/FastAPI-103/tests/test_database_service.py), we test PostgreSQL ORM operations without running a PostgreSQL instance.

#### Why `create` Used Real Inputs While `get` and `list` Used `MagicMock`
- **In `create_invoice_record()`:** Our code's job is to take raw parameters (`user`, `filename`, `file_hash`) and **construct an `InvoiceRecord`**. We must pass real inputs (`CurrentUser`, `uuid`, etc.) so we can assert that our function correctly mapped `record.user_id = user.object_id` and `record.filename = "test.pdf"`.
- **In `get_invoice_record()` and `list_user_invoices()`:** Our code does not build or modify the record; it merely runs a SQL query and returns whatever row PostgreSQL returned.
  - **The "Developer Shortcut":** Developers often write `mock_record = MagicMock()` in 1 line rather than typing out all 12 columns of `InvoiceRecord(...)` just to verify pass-through.
  - **Can you use real `InvoiceRecord` instead?** **Yes, 100%!** Using `real_record = InvoiceRecord(...)` and asserting `assert record.filename == "invoice.pdf"` is completely valid and often preferred for readability.

#### How SQLAlchemy 2.0 Query Chaining is Mocked
In SQLAlchemy 2.0 async syntax, querying the database involves method chaining:
```python
# Production code in db_invoice.py:
res = await db.execute(query)       # Step 1: db.execute returns a Result object
record = res.scalar_one_or_none()   # Step 2: Extract single record (or None)
# OR for lists:
records = list(res.scalars().all()) # Step 2: Unpack rows into a list
```

To simulate this without a database:
```python
mock_session = AsyncMock()          # Fakes AsyncSession (the DB connection)
mock_result = MagicMock()           # Fakes SQLAlchemy Result

# Single record:
mock_session.execute.return_value = mock_result
mock_result.scalar_one_or_none.return_value = mock_record

# List of records (scalars().all()):
mock_result.scalars.return_value.all.return_value = [mock_record1, mock_record2]
```

**What is being compared?**
1. `assert record == mock_record` or `assert len(records) == 2`: Verifies our function faithfully extracted and returned the database rows.
2. `mock_session.execute.assert_awaited_once()`: Verifies the function actually sent the query to PostgreSQL and awaited it (catches bugs where someone accidentally removed or skipped `db.execute`).

---

### 6. Extractor Service Mocking Rationale — Why Skip Live Calls & How to Handle Cloud SDK Objects

In [tests/test_extractor_service.py](file:///d:/Azure/FastAPI-103/tests/test_extractor_service.py), we test `_safe_float` and `ExtractionResponse` schemas rather than invoking `extract_invoice_fields()`.

#### 1. Why Not Run `extract_invoice_fields()` Directly?
Calling `extract_invoice_fields()` executes:
```python
client = _get_client(endpoint, key)
poller = client.begin_analyze_document(model_id="prebuilt-invoice", body=..., ...)
result = poller.result()  # ❌ REAL NETWORK CALL TO AZURE
```
- **Requires Live Azure Cloud:** Fails without active Azure credentials and internet access.
- **Costs Real Money:** Every call to Azure Document Intelligence burns paid API credits.
- **Slow Execution:** OCR analysis takes 3 to 8 seconds per document. Our test suite runs all 27 tests in under **1.9 seconds**.

#### 2. Did `test_invoices.py` Cover it Indirectly?
**No.** `test_invoices.py` deliberately patched out `_run_extraction`:
```python
@patch("app.routers.invoices._run_extraction", new_callable=AsyncMock)
```
API route tests only verify HTTP responses (validating PDF formats, rejecting 0-byte files with 400, returning 202 Accepted with `task_id` in <50ms, and checking Redis cache hits).

#### 3. What Would Be Needed to Mock `extract_invoice_fields()`?
Only the poller call (`poller.result()`) reaches external infrastructure. Everything else is local Python dictionary unpacking.

However, Azure's SDK does **not** return clean JSON or plain dictionaries:
- `VendorName` is not a string; it is a custom SDK `DocumentField` object.
- The code accesses properties via dot notation: `field.content`, `field.confidence`, `field.value_currency`.
- If you passed a simple dictionary like `{"VendorName": "Acme"}`, Python crashes with:  
  `AttributeError: 'str' object has no attribute 'content'`.

To mock it, you must construct a deeply nested tree of `MagicMock` or `SimpleNamespace` objects:
```python
fake_result = MagicMock()
fake_result.documents = [
    MagicMock(fields={
        "VendorName": MagicMock(content="Acme", confidence=0.98),
        "InvoiceTotal": MagicMock(value_currency=SimpleNamespace(amount=150.0)),
    })
]
```
Mocking 30+ nested SDK objects just to verify that `fields["VendorName"].content` got copied into `InvoiceFields(vendor_name=...)` creates brittle, high-maintenance mock boilerplate.

#### 4. Why Testing `_safe_float` Was the High-Leverage Decision
Azure returns monetary numbers in an unusual wrapper: `CurrencyValue(amount=199.99, currencySymbol="$")`.

If code naively does:
```python
total = float(field.value)  # 💥 Crashes: TypeError: float() argument must be a string or a real number, not 'CurrencyValue'
```

`_safe_float` is the defensive shield that safely extracts `.amount` from Azure's object, parses plain floats/strings, and returns `None` for missing or corrupted data without crashing the server. Testing every edge case of `_safe_float` protects against 95% of real-world production parsing crashes with just 4 clean unit tests.

#### Unit Tests vs. Integration Tests in Production
- **Unit Tests (Our suite):** 100% offline, deterministic, sub-second execution. Tests models, defensive helpers, validation, status codes, and error paths.
- **Integration Tests:** Run on a scheduled staging pipeline using sample invoices (`sample_invoice.pdf`) against a real Azure test resource to verify Microsoft hasn't changed their OCR model output schema.

---

### Running Tests

Install developer dependencies and execute the suite:

```pwsh
# 1. Install test dependencies
pip install -r requirements-dev.txt

# 2. Run the test suite
pytest -v

# 3. Run with coverage report
pytest --cov=app tests/
```

---

## Step 7: GitFlow & CI/CD DevSecOps Pipeline

The repository implements a **Shift-Left DevSecOps Pipeline** via GitHub Actions ([.github/workflows/ci-cd.yml](file:///d:/Azure/FastAPI-103/.github/workflows/ci-cd.yml)). Every code push and pull request undergoes automated linting, test coverage verification, static code analysis (SAST), dependency scanning, and dual-layer container vulnerability scanning before any image is published.

```
                         [ Developer Push / Pull Request ]
                                         │
                                         ▼
 ┌───────────────────────────────────────────────────────────────────────────────┐
 │ STAGE 1: Code Quality, Testing & Shift-Left Security Scans                   │
 │                                                                               │
 │  1. Checkout & Setup Python 3.12                                              │
 │  2. Install dependencies (requirements-dev.txt)                               │
 │  3. Ruff Linter & Code Formatter (fast style & syntax checks)                 │
 │  4. Pytest & Code Coverage (generates coverage.xml, 100% offline)             │
 │  5. Trivy Dependency & Secrets Scan (scans requirements.txt and git tree)     │
 │  6. SonarQube / SonarCloud Quality Gate (SAST, code smells, test coverage)   │
 └───────────────────────────────────────┬───────────────────────────────────────┘
                                         │  (All Stage 1 checks must PASS)
                                         ▼
 ┌───────────────────────────────────────────────────────────────────────────────┐
 │ STAGE 2: Container Security & Registry Artifact Delivery                      │
 │                                                                               │
 │  1. Docker Buildx with GitHub Actions Cache (builds & loads image locally)   │
 │  2. Trivy Container Image Scan (scans base OS packages and layers for CVEs)  │
 │  3. Docker Hub Push via build-push-action (instant push using cached layers) │
 └───────────────────────────────────────┬───────────────────────────────────────┘
                                         │  (Vulnerabilities resolved)
                                         ▼
 ┌───────────────────────────────────────────────────────────────────────────────┐
 │ STAGE 3: Infrastructure & Production Deployment (Gated)                       │
 │                                                                               │
 │  1. Terraform Apply (Validates and applies cloud resources)                   │
 │  2. Alembic Migrations (Applies PostgreSQL schema upgrades)                   │
 │  3. Azure App Service Deploy (Pulls verified image from Docker Hub)           │
 └───────────────────────────────────────────────────────────────────────────────┘
```

---

### 1. GitFlow Branching Strategy

| Branch | Purpose | Protection Rules |
| :--- | :--- | :--- |
| `main` | Production releases. Always stable and deployable. | Direct pushes blocked. Requires PR review and green CI checks. |
| `develop` | Integration / Staging branch where new features merge. | Requires green CI checks before merging into `main`. |
| `feature/*` | Ephemeral feature branches (e.g. `feature/jwt-rotation`). | Cut from `develop`, merges back into `develop` via PR. |
| `hotfix/*` | Critical production fixes cut directly from `main`. | Merges into both `main` and `develop`. |

---

### 2. Required GitHub Secrets

To enable Docker Hub publishing, SonarCloud analysis, and automated Azure deployment, configure the following secrets in your GitHub repository (**Settings > Secrets and variables > Actions > New repository secret**):

| Secret Name | Description | Where to Obtain / Command |
| :--- | :--- | :--- |
| `DOCKERHUB_USERNAME` | Your Docker Hub account username | [hub.docker.com](https://hub.docker.com) |
| `DOCKERHUB_TOKEN` | Docker Hub Personal Access Token (Read & Write) | Docker Hub Account Settings > Security > New Access Token |
| `SONAR_TOKEN` | SonarCloud Authentication Token | [sonarcloud.io](https://sonarcloud.io) > User Profile > My Account > Security > Generate Token |
| `AZURE_CREDENTIALS` | Service Principal JSON credentials for Azure login | `az ad sp create-for-rbac --name "sp-fastapi-invoice-cicd" --role Contributor --scopes "/subscriptions/<SUBSCRIPTION_ID>/resourceGroups/rg-invoice-dev" --sdk-auth` |
| `AZURE_WEBAPP_NAME` | Name of the Azure Linux Web App (default: `app-invoice-dev`) | Azure Portal > App Services or Terraform output |
| `DATABASE_URL` | Production PostgreSQL connection string with SSL | `postgresql+asyncpg://<user>:<password>@<db-host>:5432/<dbname>?ssl=require` |

> [!NOTE]
> - SonarCloud Organization: `abhishek-sunkale`
> - SonarCloud Project Key: `abhishek6969_FastAPI-103`
> - `GITHUB_TOKEN` is automatically injected by GitHub Actions to report PR quality gate statuses.
> - If `DOCKERHUB_USERNAME`, `SONAR_TOKEN`, `AZURE_CREDENTIALS`, or `DATABASE_URL` is not yet configured, the workflow gracefully skips optional remote steps without failing local builds, tests, and security scans.

---

### 3. Tool Responsibilities & Why We Avoid Duplication

| Security Layer | Tool | What It Scans | Why We Chose It |
| :--- | :--- | :--- | :--- |
| **Linting & Formatting** | **Ruff** | Code style, syntax errors, unused imports | Written in Rust; 100x faster than Flake8/Black. |
| **Testing & Coverage** | **Pytest + Pytest-Cov** | All 27 unit tests; outputs `coverage.xml` | Offline, deterministic (<2s). Generates coverage for SonarQube. |
| **SAST & Code Quality** | **SonarQube / SonarCloud** | Python source files (`app/`) | Full OWASP Top 10 SAST engine + technical debt metrics. (Eliminates redundant Bandit). |
| **SCA & Secret Scanning** | **Trivy (Filesystem)** | `requirements.txt`, git commit tree | Native dependency vulnerability analysis + secret leak detection. (Eliminates redundant pip-audit). |
| **Container CVE Scanning** | **Trivy (Image)** | Docker container image layers | Scans Debian OS packages (`openssl`, `glibc`, `curl`) before pushing to Docker Hub. |
| **Container Delivery** | **`docker/build-push-action`** | Dockerfile | Official GitHub Action with GHA layer caching (`cache-from/to: type=gha`). |

---

### 4. Anatomy of the Test Command

```pwsh
pytest -v --cov=app --cov-report=xml:coverage.xml --cov-report=term tests/
```

- `pytest`: The Python test runner.
- `-v` (verbose): Prints each individual test name and its `PASSED` / `FAILED` status instead of cryptic dots (`....`).
- `--cov=app`: Measures line and branch test coverage specifically for code inside `app/` (ignoring external libraries and the tests themselves).
- `--cov-report=xml:coverage.xml`: Exports the coverage report in Cobertura XML format into `coverage.xml`. SonarQube requires this exact XML file to display code coverage percentages and highlighted untested lines on its dashboard.
- `--cov-report=term`: Prints a formatted coverage summary table in the terminal/CI console showing percentage covered per file.
- `tests/`: Tells pytest to discover and execute all test files inside the `tests/` directory.

---

### 5. Running Local Checks Before Pushing

```pwsh
# 1. Run linter
ruff check app/ tests/

# 2. Run unit tests with coverage
pytest -v --cov=app --cov-report=term tests/

# 3. Run Trivy filesystem scan (if Trivy CLI is installed locally)
trivy fs --scanners vuln,secret .
```

---

### 6. Real-World DevSecOps Troubleshooting & Hardening Lessons

During pipeline integration, multiple real-world security gates and configuration hurdles were identified and hardened:

#### Lesson 1: Docker Image Secret Leaks from SSH Host Keys (`AsymmetricPrivateKey`)
- **The Issue:** Trivy flagged `/etc/ssh/ssh_host_rsa_key`, `ssh_host_ecdsa_key`, and `ssh_host_ed25519_key` as `HIGH` severity secret leaks inside the container image.
- **Root Cause:** When `apt-get install -y openssh-server` runs during `docker build`, Debian’s package manager generates static SSH host private keys and bakes them permanently into the Docker image layers. This is insecure because every running container would share identical private keys.
- **The Solution:**
  1. In [Dockerfile](file:///d:/Azure/FastAPI-103/Dockerfile#L16), we purge build-time keys:  
     `rm -f /etc/ssh/ssh_host_*`
  2. In [entrypoint.sh](file:///d:/Azure/FastAPI-103/entrypoint.sh#L9-L13), we generate fresh, unique host keys dynamically at container boot:  
     `if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then ssh-keygen -A; fi`
  - **Outcome:** The static Docker image has **0 private keys**, Trivy secret scans pass cleanly, and Azure App Service Web SSH functions securely with dynamically generated keys.

#### Lesson 2: Transitive Dependency CVEs (`starlette`)
- **The Issue:** Trivy reported 3 `HIGH` severity CVEs against `starlette` (`CVE-2024-47874`, `CVE-2026-48818`, `CVE-2026-54283`).
- **Root Cause:** While direct dependencies were updated, `fastapi==0.115.0` was pulling in an unpinned, vulnerable transitive version of Starlette (`0.38.6`).
- **The Solution:** Bumped `fastapi>=0.115.6` and explicitly pinned `starlette>=0.41.2` in [requirements.txt](file:///d:/Azure/FastAPI-103/requirements.txt), immediately eliminating all High and Critical CVEs from the dependency tree.

#### Lesson 3: SonarCloud Container Path Mapping in GitHub Actions
- **The Issue:** SonarCloud reported `Coverage: 0.0%` with the warning:  
  `Invalid directory path in 'source' element: /home/runner/work/FastAPI-103/FastAPI-103/app`
- **Root Cause:** Pytest runs on the GitHub Actions host runner (`/home/runner/work/...`), but `sonarcloud-github-action` runs inside an isolated Docker container where the repository is mounted at `/github/workspace`. Because the absolute host path did not exist inside the container, SonarCloud could not resolve the 15 source files.
- **The Solution:** We run `pytest --cov=.` and normalize the coverage path with a one-line `sed` command before SonarCloud executes:  
  `sed -i 's|<source>.*</source>|<source>/github/workspace</source>|g' coverage.xml`  
  This aligns with `sonar.sources=.` and `sonar.inclusions=app/**`, allowing SonarCloud to resolve 100% of source files and display true coverage metrics.

#### Lesson 4: Pipeline Trigger Efficiency (`paths-ignore`)
- **The Need:** Pushing documentation updates (like `README.md`) or modifying Terraform infrastructure configurations should not trigger a full Docker build, unit test suite, and image scan.
- **The Solution:** Added `paths-ignore` to `.github/workflows/ci-cd.yml`:
  ```yaml
  paths-ignore:
    - "terraform/**"
    - "**.md"
    - ".gitignore"
  ```
  This saves GitHub Actions compute minutes and prevents redundant container builds while managing infrastructure separately.

#### Lesson 5: Pre-Deployment Database Migrations with Alembic in CI/CD
- **The Issue:** Running database migrations inside the Docker container entrypoint (`entrypoint.sh`) at startup causes critical operational hazards in production:
  1. **Concurrency Conflicts:** When App Service scales or boots multiple container replicas simultaneously, each worker runs `alembic upgrade head` concurrently, risking database schema locks and race conditions.
  2. **Crash Loops:** If a migration fails on boot, the container exits immediately, leading to App Service restart loops and total downtime for live traffic.
- **The Solution:** We decoupled database migrations from container boot. In Stage 3 of [.github/workflows/ci-cd.yml](file:///d:/Azure/FastAPI-103/.github/workflows/ci-cd.yml), GitHub Actions runs `alembic upgrade head` in a single isolated runner step *before* triggering the container update:
  ```bash
  alembic upgrade head
  ```
  If migrations encounter an error, the deployment aborts immediately, leaving the existing live container completely untouched and operational.

#### Lesson 6: Zero-Downtime Container Deployments to Azure App Service via Service Principal (SP)
- **The Workflow:**
  1. **Authentication:** GitHub Actions authenticates against Azure Resource Manager using a dedicated Service Principal (`azure/login@v2`) scoped to the resource group (`rg-invoice-dev`).
  2. **Container Rollout:** Microsoft's official `azure/webapps-deploy@v3` action updates the App Service configuration to the freshly built, Trivy-scanned Docker image (`${{ secrets.DOCKERHUB_USERNAME }}/fastapi-invoice-api:latest`).
  3. **Health Validation:** Azure App Service performs a zero-downtime rolling container swap, keeping existing requests alive until the new container passes health probes on port 8000.

#### Lesson 7: Decoupling Cloud Infrastructure from Application Delivery
- **The Practice:** Infrastructure code (`terraform/**`) and application code (`app/**`) possess different lifecycles and risk profiles:
  - Infrastructure changes (PostgreSQL firewall rules, Redis cache tiering, Cognitive Services SKUs) are stateful, high-impact operations managed deliberately via the Terraform CLI.
  - Application CI/CD concentrates on rapid feedback, unit testing, SAST/SCA security gates, container image creation, and automated deployment.
  - By ignoring `terraform/**` in application CI/CD, developers avoid unintentional infra mutations while maintaining rapid, secure application release velocity.

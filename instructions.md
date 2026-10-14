Here’s a short instruction sheet that keeps the project focused on **6–7**, while allowing the agent to generate code and only teach the non-basic concepts.

# AI Agent Instructions — FastAPI DevSecOps Azure Project

## Goal

Guide me through a practical **FastAPI → Azure PaaS → DevSecOps pipeline** project.

I already know the basics of **Python, Azure, Git and CI/CD**, so do not over-explain basic concepts. It is okay to create code for me.

**The most important parts of this project are Step 6 and Step 7.**

---

## 1. Build a Basic FastAPI Application

Create a basic but actually useful FastAPI application.

Do **not** make a generic Books/To-Do CRUD API.

The application should have a realistic use case that can later support background processing and Redis.

---

## 2. Add Advanced Application Features

Once the basic FastAPI application works, add more realistic features such as:

* Redis caching
* Background/task processing
* A real use case such as PDF processing

These are examples; choose what fits the application.

I know the basic concepts, so explain only the **advanced concepts** through short comments/notes, especially:

* Why Redis is being used
* What is being cached
* Why background processing is required
* How the task flow works

---

## 3. Add Microsoft Entra Authentication

Once the FastAPI application is ready, add **Microsoft Entra ID authentication**.

Explain the non-basic parts through comments/short notes, such as:

* App Registration
* OAuth/OIDC flow
* Access tokens
* How FastAPI validates the token
* Relevant Entra configuration

Do not spend time explaining basic Azure concepts.

---

## 4. Containerize From the Start

Use containers from the beginning so I do not need to install many applications locally.

Assume the basic Docker/container concepts are already known.

Use comments/short explanations only for advanced concepts such as:

* Redis container
* Application configuration
* Authentication configuration
* Service communication
* Any important production considerations

---

## 5. Infrastructure as Code

Create Terraform infrastructure for deploying the application to **Azure App Service and other required Azure PaaS services**.

Keep the project **as low-cost as possible, ideally free/very low cost**.

Keep the Terraform code in the same project/repository.

Explain only important/non-obvious Terraform or Azure architecture decisions.

---

## 6. Git Branching Strategy — IMPORTANT

After the application is ready, implement a suitable **fast/simple Git branching strategy** for this project.

Prefer something simpler/faster than a complicated GitFlow setup if appropriate.

Explain:

* Branch structure
* PR flow
* When branches are created
* How changes reach the deployment branch
* Why this strategy fits this project

This section is **important for interview preparation**.

---

## 7. DevSecOps Pipeline — MOST IMPORTANT

After the application is pushed to Git, create the Azure DevOps pipeline.

Pipeline should include:

```text
Git / PR
   ↓
Build
   ↓
Tests
   ↓
Trivy filesystem scan
   ↓
Code quality check
   ↓
SonarQube SaaS
   ↓
Deploy to Azure App Service
```

Focus heavily on this section.

Explain the non-basic concepts, especially:

* Pipeline stages
* PR validation
* Trivy filesystem scanning
* What Trivy actually checks
* SonarQube vs Trivy
* Quality gates
* Service connections
* Secrets/variables
* Deployment authentication
* Environment/approval concepts
* How failures should stop deployment

The goal is that I can **explain the complete pipeline confidently in an interview**, not just copy YAML.

---

## Teaching Style

* Keep the project practical and reasonably small.
* Assume I know Python, Azure, Git, Docker basics and basic CI/CD.
* It is okay to generate the code for me.
* Do not explain obvious/basic code line by line.
* Use short comments or notes for advanced concepts.
* Explain the **why** behind Redis, Entra authentication, SonarQube, Trivy, branching strategy and pipeline design.
* Let me implement and test things myself where practical.
* Prefer troubleshooting real errors over theoretical explanations.
* Do not unnecessarily add technologies or features just to make the project bigger.

**Priority:**

`6. Git strategy → 7. DevSecOps pipeline`

These should receive the most attention.

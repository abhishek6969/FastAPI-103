resource "azurerm_cognitive_account" "document_intelligence" {
  name                = "docintel-${local.name_prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  kind                = "FormRecognizer"
  sku_name            = "S0"
  # F0 free tier: 500 pages/month, 20 requests/minute, 1 transaction/second.
  # Upgrade to S0 (~$1.50/1000 pages) when exceeding free limits.

  tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }
}

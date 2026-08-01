# Functional Analysis: Shopify Variant Reset Script

**Version**: 3.0  
**Date**: 25 November 2025  
**Author**: Racoon s.r.l.  
**System**: Shopify Variant Reset & Inventory Management

---

## 1. OVERVIEW

### 1.1 Purpose
Python script for full deletion and recreation of Shopify product variants, preserving all original data including multi-location inventory levels.

### 1.2 Operational Context
Used for:
- Variant reordering when Shopify API does not allow in-place edits
- Fixing data corruption in variants
- Workaround for API limitations on products with metafields linked to options
- Cleanup and normalization of variant structure

### 1.3 Runtime Environment
- **Platform**: Render.com (cron service)
- **Language**: Python 3.x
- **Database**: MySQL for temporary backup
- **API**: Shopify Admin REST API v2024-04

---

## 2. SYSTEM ARCHITECTURE

### 2.1 Tech Stack

```
┌─────────────────────────────────────────┐
│         Render Cron Service             │
│   (Scheduled or manual execution)       │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│      reset_variants.py (Python)         │
│  ┌────────────────────────────────┐     │
│  │  • Fetch variants from Shopify │     │
│  │  • Backup DB (MySQL temporary) │     │
│  │  • Delete & Recreate variants  │     │
│  │  • Restore inventory levels    │     │
│  │  • Cleanup unused locations    │     │
│  └────────────────────────────────┘     │
└──────┬────────────────────┬─────────────┘
       │                    │
       ▼                    ▼
┌─────────────┐      ┌──────────────┐
│  Shopify    │      │   MySQL DB   │
│  Admin API  │      │  (Temporary  │
│             │      │   Tables)    │
└─────────────┘      └──────────────┘
```

### 2.2 Dependencies
```python
requests                 # HTTP API calls to Shopify
mysql-connector-python   # MySQL database connection
```

---

## 3. CONFIGURATION

### 3.1 Required Environment Variables

| Variable | Description | Example |
|-----------|-------------|---------|
| `SHOPIFY_DOMAIN` | Shopify store domain | `racoon-lab.myshopify.com` |
| `SHOPIFY_TOKEN` | Admin API access token | `shpat_xxxxxxxxxxxxx` |
| `DB_HOST` | MySQL database host | `mysql.render.com` |
| `DB_USER` | Database username | `admin` |
| `DB_PASS` | Database password | `********` |
| `DB_NAME` | Database name | `shopify_sync` |
| `PRODUCT_IDS` | Product IDs (comma-separated) | `15389702455628,12345678` |

### 3.2 Config Files

**render.yaml**
```yaml
services:
  - type: cron
    name: reset-variants
    schedule: "0 0 31 2 *"  # Never - manual trigger only
    runtime: python
    buildCommand: "pip install -r requirements.txt"
    startCommand: "python reset_variants.py"
```

**requirements.txt**
```
requests
mysql-connector-python
```

---

## 4. DETAILED OPERATIONAL FLOW

### 4.1 Logical Overview

```
START
  │
  ├─ DB connection & temporary tables setup
  │
  └─ For each PRODUCT_ID:
       │
       ├─ STEP 1: Fetch all variants from Shopify
       │
       ├─ STEP 2: Backup to MySQL
       │    ├─ Variant data (full JSON)
       │    └─ Inventory levels (all locations)
       │
       ├─ STEP 3: Delete variants 2-N
       │
       ├─ STEP 4: Recreate variants 2-N from backup
       │    └─ Skip variants with "perso" in the title
       │
       ├─ STEP 5: Delete variant #1
       │
       ├─ STEP 6: Recreate variant #1 from backup
       │    └─ Skip if it contains "perso"
       │
       ├─ STEP 7: Restore inventory levels
       │    └─ For each original location
       │
       └─ STEP 8: Cleanup extra locations
            └─ Remove locations not present in the original
END
```

### 4.2 STEP 1: Fetch Variants

**Endpoint**: `GET /admin/api/2024-04/products/{id}/variants.json`

**Data retrieved per variant**:
- `id`: unique variant ID
- `inventory_item_id`: inventory item ID
- `option1`, `option2`, `option3`: option values
- `price`, `compare_at_price`: prices
- `sku`, `barcode`: identifier codes
- `inventory_management`: inventory tracking (shopify/null)
- `inventory_policy`: sales policy (deny/continue)
- `weight`, `weight_unit`: product weight
- Other fields (taxable, requires_shipping, etc.)

### 4.3 STEP 2: Database Backup

**Temporary tables created**:

```sql
-- Variant backup
CREATE TEMPORARY TABLE variant_backup (
    id BIGINT,
    product_id BIGINT,
    inventory_item_id BIGINT,
    variant_json TEXT,           -- Full variant JSON
    position INT,                -- Original position
    PRIMARY KEY (product_id, id)
);

-- Inventory levels backup
CREATE TEMPORARY TABLE inventory_backup (
    variant_id BIGINT,
    inventory_item_id BIGINT,
    location_id BIGINT,          -- Shopify location ID
    available INT,               -- Available quantity
    PRIMARY KEY (variant_id, location_id)
);
```

**Inventory backup process**:
1. For each variant with `inventory_management != null`
2. Call `GET /inventory_levels.json?inventory_item_ids={id}`
3. Save **all** locations with their quantities

### 4.4 STEP 3-6: Delete & Recreate Strategy

**Logic without DUMMY** (metafield-compatible):

```
Original variants: [V1, V2, V3, ..., VN]

1. Delete V2-VN     → Only [V1] remains
2. Recreate V2-VN   → Now: [V1, V2', V3', ..., VN']
3. Delete V1        → Remaining: [V2', V3', ..., VN']
4. Recreate V1      → Final: [V1', V2', V3', ..., VN']
```

**Reason for the strategy**:
Shopify always requires at least 1 active variant. It is not possible to delete all variants simultaneously.

**"perso" filter**:
```python
if "perso" in v.get("title", "").lower():
    # Skip recreation of this variant
    continue
```

### 4.5 STEP 7: Inventory Restore

**Endpoint**: `POST /admin/api/2024-04/inventory_levels/set.json`

**Payload**:
```json
{
  "location_id": 8251572336,
  "inventory_item_id": 55507789152588,
  "available": 1
}
```

**Variant mapping**:
- Dictionary `{old_variant_id: new_inventory_item_id}`
- DB query: retrieve location and quantity for `old_variant_id`
- Set inventory on the `new_inventory_item_id`

### 4.6 STEP 8: Extra Location Cleanup

**Problem solved**:
When Shopify creates a new variant with active inventory_management, it automatically creates inventory_levels for **all** locations available in the store (default 0).

**Solution**:
1. DB query: which locations did the original variant have?
2. Current fetch: which locations does the new variant have?
3. For each location NOT present in the original → DELETE

**Endpoint**: `DELETE /admin/api/2024-04/inventory_levels.json?inventory_item_id={id}&location_id={loc}`

**Result**:
- Original locations: kept with correct quantities
- Extra locations: removed → "Not stocked" state in Shopify

---

## 5. ERROR HANDLING AND RESILIENCE

### 5.1 Rate Limiting

**Exponential Backoff Strategy**:
```python
def safe_request(method, url, max_retries=5):
    for attempt in range(max_retries):
        if res.status_code == 429:
            wait = 2 ** attempt  # 1s, 2s, 4s, 8s, 16s
            sleep(wait)
            continue
```

**Shopify limits**:
- 2 calls/second per endpoint (leaky bucket)
- 0.6s sleep between consecutive calls (safety margin)

### 5.2 HTTP Errors

**Handled codes**:
- `429`: rate limit → retry with backoff
- `422`: validation error → detailed log + skip
- `4xx/5xx`: other errors → log + exception

**Error logging**:
```python
try:
    error_detail = res.json()
    log(f"❌ Errore API {res.status_code}: {json.dumps(error_detail, indent=2)}")
except:
    log(f"❌ Errore API {res.status_code}: {res.text}")
```

### 5.3 Database Connection

**Auto-reconnect**: not implemented (short execution)  
**Transactions**: explicit commit after each backup  
**Temporary tables**: auto-destroyed at end of session

---

## 6. LOGGING AND MONITORING

### 6.1 Log Format

```
[YYYY-MM-DD HH:MM:SS] {emoji} Message
```

**Emojis used**:
- 📦 Product processing
- 🔍 Variants found
- 💾 Data backup
- 🗑️ Deletion
- 🔄 Recreation
- 📍 Inventory operations
- 🧹 Cleanup
- ✅ Success
- ❌ Error
- ⚠️ Warning
- ⏭️ Skip

### 6.2 Detail Levels

**Level 1 - Product**:
```
[timestamp] 📦 Elaborazione prodotto: 15389702455628
[timestamp] ✅ Prodotto 15389702455628 completato con successo!
```

**Level 2 - Step**:
```
[timestamp] 🔍 Trovate 12 varianti
[timestamp] 💾 Backup varianti e inventory levels...
[timestamp] 🗑️ Cancellazione varianti dalla 2 alla N...
```

**Level 3 - Detail**:
```
[timestamp]   💾 Backup inventory: variant 56062973968716, location 8251572336, qty 0
[timestamp]   ✅ Cancellata variante 56062975082828 (Outlet - 42)
[timestamp]   🔄 Ripristino inventory: location 8251572336, qty 1
```

---

## 7. USE CASES AND SCENARIOS

### 7.1 Case A: Standard Product (no metafield)

**Characteristics**:
- Sizes NOT linked to metafields
- Inventory in "magazzino" location
- All variants to keep

**Behavior**:
✅ Delete & recreate with no errors  
✅ Inventory correctly restored  
✅ "magazzino" location kept  
✅ Extra locations removed

### 7.2 Case B: Product with Option Metafield

**Characteristics**:
- Sizes linked to Shopify metafields
- Inventory in "promo" location
- Some variants with "perso" to skip

**Behavior**:
✅ Non-DUMMY strategy works (no option-value edits)  
✅ Inventory in "promo" restored  
✅ "magazzino" location removed → "Not stocked"  
✅ "perso" variants not recreated

### 7.3 Case C: Multi-Location Product

**Characteristics**:
- Inventory spread across 3+ locations
- Different quantities per location
- Mix of variants with/without inventory tracking

**Behavior**:
✅ All original locations kept  
✅ Correct quantities per location  
✅ Variants without tracking → skip cleanup  
✅ Extra locations removed

---

## 8. LIMITATIONS AND CONSTRAINTS

### 8.1 Technical Limitations

1. **Shopify API Limits**:
   - Max 2 req/sec per endpoint
   - Timeout after 5 retries (max 31 seconds)

2. **Variants with Images**:
   - Image-variant associations are NOT preserved
   - Manual association required post-script

3. **Variant Metafields**:
   - Only product/option metafields are handled
   - Custom variant metafields are NOT copied

4. **MySQL Limits**:
   - Temporary tables: max session lifetime
   - TEXT field: max 65KB per variant_json

### 8.2 Operational Constraints

1. **Product IDs**:
   - Must be valid and accessible with the provided token
   - Product must have at least 1 variant

2. **Database**:
   - Must be accessible throughout execution
   - Permissions: CREATE TEMPORARY TABLE, SELECT, INSERT, DELETE

3. **Execution Time**:
   - ~1 minute per product with 10 variants
   - ~30 seconds for inventory backup/restore
   - Render timeout: 300s (5 minutes) per cron job

---

## 9. TROUBLESHOOTING

### 9.1 Common Errors

#### Error: "Cannot set name for an option value linked to a metafield"

**Cause**: product with metafield on option  
**Fix**: already handled — non-DUMMY strategy  
**Verify**: log shows delete 2-N → recreate 2-N → delete 1 → recreate 1

#### Error: "The variant 'X' already exists"

**Cause**: variant not properly deleted in previous step  
**Fix**:
1. Check step 3/5 logs (deletion)
2. Verify Shopify token permissions
3. Manual retry

#### Error: "422 Client Error" during recreation

**Cause**: invalid variant data (duplicate SKU, invalid option, etc.)  
**Log**: shows Shopify JSON error details  
**Fix**: correct data in original product, then retry

### 9.2 Incorrect Inventory

#### "Magazzino" location at 0 instead of "Not stocked"

**Cause**: STEP 8 (cleanup) not executed correctly  
**Verify**: search logs for "🧹 Pulizia location inventory"  
**Fix**:
1. Verify inventory_backup contains data
2. Check correct variant_id mapping
3. Manual cleanup via Shopify admin if needed

#### Quantities lost after script

**Cause**: inventory backup not saved (inventory_management = null)  
**Verify**: log must show "💾 Backup inventory" for each variant  
**Fix**: verify original variants had active inventory_management

### 9.3 Performance Issues

#### Slow script (>5 minutes)

**Cause**: too many products or variants  
**Fix**:
1. Reduce number of products in PRODUCT_IDS
2. Increase Render timeout (max 900s for web service)
3. Optimize sleep time (rate-limit risk)

#### Frequent rate limits

**Cause**: insufficient sleep time  
**Fix**: increase sleep from 0.6s to 1.0s

---

## 10. MAINTENANCE AND EVOLUTION

### 10.1 Shopify API Updates

**Current version**: 2024-04  
**Next update**: check deprecations every 3 months  
**Breaking changes**: test on dev store before production

### 10.2 Continuous Monitoring

**KPIs to track**:
- Average processing time per product
- 422 (validation) error rate
- Inventory restore success rate
- Number of location cleanups per run

### 10.3 Backup and Recovery

**Automatic backup**:
- Temporary MySQL tables → NOT persistent
- Consider pre-script dump for critical products

**Recovery procedure**:
1. Identify corrupted product
2. Recover original data from store history/backup
3. Manual fix via Shopify admin
4. Retry script

---

## 11. SECURITY AND COMPLIANCE

### 11.1 Credential Management

- ✅ API token in environment variables (not hardcoded)
- ✅ Database password in environment variables
- ⚠️ Logging must NOT include token/password

### 11.2 Data Access

**Required Shopify token scopes**:
- `read_products`
- `write_products`
- `read_inventory`
- `write_inventory`

**Database permissions**:
- CREATE TEMPORARY TABLE
- SELECT, INSERT, DELETE on temporary tables

### 11.3 Audit Trail

**Logs retained on**:
- Render dashboard (7 days)
- Consider exporting to external storage for audit

---

## 12. CONTACTS AND SUPPORT

**Maintainer**: Racoon s.r.l.  
**Platform**: Render.com  
**Store**: racoon-lab.myshopify.com  
**Database**: MySQL on Render

**External documentation**:
- [Shopify Admin API](https://shopify.dev/api/admin-rest)
- [Inventory Management](https://shopify.dev/api/admin-rest/2024-04/resources/inventorylevel)
- [Render Cron Jobs](https://render.com/docs/cronjobs)

---

## CHANGELOG

### v3.0 (2025-11-25)
- ✅ Added extra location inventory cleanup (STEP 8)
- ✅ Fix: direct DB query for original_locations
- ✅ Detailed logging for cleanup debugging

### v2.0 (2025-11-25)
- ✅ Removed DUMMY strategy (metafield compatibility)
- ✅ New logic: delete 2-N → recreate 2-N → delete 1 → recreate 1
- ✅ Added "perso" filter on variant title

### v1.0 (2025-11-25)
- ✅ Initial implementation with DUMMY strategy
- ✅ Multi-location inventory backup
- ✅ Inventory level restore

---

**End of Document**

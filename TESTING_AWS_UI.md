# AWS UI Testing - Copy & Paste Ready

Quick reference with values ready to paste into AWS Console.

## Prerequisites

Before testing, note these values:
- **API Endpoint**: From CloudFormation outputs (e.g., `https://xyz123.execute-api.us-east-1.amazonaws.com/Prod`)
- **User ID**: From database (e.g., `1`)
- **Product IDs**: From database (e.g., `1`, `2`, `3`)

---

## API Gateway Test Console

Go to: **API Gateway → APIs → ChonkychonkAPI → Resources → {resource} → Method**

### Test Products GET

**Resource**: `/products`  
**Method**: `GET`

1. Click **Test** button
2. Leave all fields blank
3. Click **Test**

**Expected Status**: `200`

---

### Test Products by ID GET

**Resource**: `/products/{productid}`  
**Method**: `GET`

1. Click **Test** button
2. **Path Parameters** → `productid`: `1`
3. Click **Test**

**Expected Status**: `200`

---

### Test Orders POST (Create)

**Resource**: `/orders`  
**Method**: `POST`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   Content-Type: application/json
   ```
3. **Request Body**:
   ```json
   {
     "user_id": 1,
     "items": [
       {"product_id": 1, "quantity": 2},
       {"product_id": 2, "quantity": 1}
     ],
     "tax_rate": 0.13,
     "shipping_cost": 10.00,
     "customer_notes": "Please leave at door"
   }
   ```
4. Click **Test**

**Expected Status**: `201`  
**Note the returned `order_id`** (e.g., `1`)

---

### Test Orders GET (Retrieve)

**Resource**: `/orders/{orderId}`  
**Method**: `GET`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   ```
3. **Path Parameters** → `orderId`: `1` (replace with your order_id)
4. Click **Test**

**Expected Status**: `200`

---

### Test Orders PUT (Update)

**Resource**: `/orders/{orderId}`  
**Method**: `PUT`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   Content-Type: application/json
   ```
3. **Path Parameters** → `orderId`: `1`
4. **Request Body**:
   ```json
   {
     "items": [
       {"product_id": 1, "quantity": 3}
     ]
   }
   ```
5. Click **Test**

**Expected Status**: `200`

---

### Test Orders DELETE (Soft Delete)

**Resource**: `/orders/{orderId}`  
**Method**: `DELETE`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   ```
3. **Path Parameters** → `orderId`: `1`
4. Click **Test**

**Expected Status**: `200`

---

## Lambda Test Console

Go to: **Lambda → Functions → {FunctionName} → Test**

### Test Orders Function - Create Order

**Function**: `chonkychonk-orders-dev`  
**Event name**: `create-order`

1. Click **Create new event**
2. **Event source**: Lambda
3. **Event name**: `create-order`
4. **Template**: `apigateway-aws-proxy`
5. Replace the JSON with:

```json
{
  "httpMethod": "POST",
  "path": "/orders",
  "headers": {
    "X-User-Id": "1",
    "Content-Type": "application/json"
  },
  "body": "{\"user_id\": 1, \"items\": [{\"product_id\": 1, \"quantity\": 2}, {\"product_id\": 2, \"quantity\": 1}], \"tax_rate\": 0.13, \"shipping_cost\": 10.00, \"customer_notes\": \"Please leave at door\"}",
  "isBase64Encoded": false
}
```

6. Click **Save**
7. Click **Test**

**Expected**: Status 201, order_id in response

---

### Test Orders Function - Get Order

**Function**: `chonkychonk-orders-dev`  
**Event name**: `get-order`

1. Click **Create new event**
2. **Event name**: `get-order`
3. Replace with:

```json
{
  "httpMethod": "GET",
  "path": "/orders/1",
  "pathParameters": {
    "orderId": "1"
  },
  "headers": {
    "X-User-Id": "1"
  },
  "body": null,
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, order data in response

---

### Test Orders Function - Update Order

**Function**: `chonkychonk-orders-dev`  
**Event name**: `update-order`

1. Click **Create new event**
2. **Event name**: `update-order`
3. Replace with:

```json
{
  "httpMethod": "PUT",
  "path": "/orders/1",
  "pathParameters": {
    "orderId": "1"
  },
  "headers": {
    "X-User-Id": "1",
    "Content-Type": "application/json"
  },
  "body": "{\"items\": [{\"product_id\": 1, \"quantity\": 3}]}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, updated totals in response

---

### Test Orders Function - Delete Order

**Function**: `chonkychonk-orders-dev`  
**Event name**: `delete-order`

1. Click **Create new event**
2. **Event name**: `delete-order`
3. Replace with:

```json
{
  "httpMethod": "DELETE",
  "path": "/orders/1",
  "pathParameters": {
    "orderId": "1"
  },
  "headers": {
    "X-User-Id": "1"
  },
  "body": null,
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, success message

---

### Test Orders Function - Error Case (Invalid Product)

**Function**: `chonkychonk-orders-dev`  
**Event name**: `error-invalid-product`

1. Click **Create new event**
2. **Event name**: `error-invalid-product`
3. Replace with:

```json
{
  "httpMethod": "POST",
  "path": "/orders",
  "headers": {
    "X-User-Id": "1",
    "Content-Type": "application/json"
  },
  "body": "{\"user_id\": 1, \"items\": [{\"product_id\": 9999, \"quantity\": 1}]}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 400, error message "Product 9999 not found"

---

### Test Orders Function - Error Case (Insufficient Stock)

**Function**: `chonkychonk-orders-dev`  
**Event name**: `error-insufficient-stock`

1. Click **Create new event**
2. **Event name**: `error-insufficient-stock`
3. Replace with:

```json
{
  "httpMethod": "POST",
  "path": "/orders",
  "headers": {
    "X-User-Id": "1",
    "Content-Type": "application/json"
  },
  "body": "{\"user_id\": 1, \"items\": [{\"product_id\": 1, \"quantity\": 999}]}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 400, error message about insufficient stock

---

### Test Orders Function - Error Case (Missing User ID)

**Function**: `chonkychonk-orders-dev`  
**Event name**: `error-missing-user-id`

1. Click **Create new event**
2. **Event name**: `error-missing-user-id`
3. Replace with:

```json
{
  "httpMethod": "POST",
  "path": "/orders",
  "headers": {
    "Content-Type": "application/json"
  },
  "body": "{\"items\": [{\"product_id\": 1, \"quantity\": 1}]}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 401, error message about missing user_id

---

### Test Products Function - Get Products

**Function**: `chonkychonk-products-dev`  
**Event name**: `get-products`

1. Click **Create new event**
2. **Event name**: `get-products`
3. Replace with:

```json
{
  "httpMethod": "GET",
  "path": "/products",
  "headers": {},
  "body": null,
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, product list in response

---

### Test Products Function - Get Single Product

**Function**: `chonkychonk-products-dev`  
**Event name**: `get-product-by-id`

1. Click **Create new event**
2. **Event name**: `get-product-by-id`
3. Replace with:

```json
{
  "httpMethod": "GET",
  "path": "/products/1",
  "pathParameters": {
    "productid": "1"
  },
  "headers": {},
  "body": null,
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, single product data

---

## CloudWatch Logs Console

Go to: **CloudWatch → Logs → Log Groups**

### View Orders Lambda Logs

1. Search: `/aws/lambda/chonkychonk-orders-dev`
2. Click the log group
3. View recent log streams
4. Look for `[DEBUG]`, `[ERROR]`, `[INFO]` entries

---

### View Products Lambda Logs

1. Search: `/aws/lambda/chonkychonk-products-dev`
2. Click the log group
3. View recent log streams

---

### Filter for Errors

1. In log group, click **Logs Insights**
2. Paste query:
   ```
   fields @timestamp, @message
   | filter @message like /ERROR/
   | stats count() as errors by @message
   ```
3. Click **Run query**

---

## RDS Query Console

Go to: **RDS → Databases → chonky-instance → Query Editor**

### View Recent Orders

```sql
SELECT id, user_id, status, total_amount, created_at, deleted_at
FROM orders
ORDER BY created_at DESC
LIMIT 10;
```

Copy and paste this into Query Editor and run.

---

### View Order Items

```sql
SELECT oi.id, oi.order_id, oi.product_id, oi.quantity, oi.unit_price, oi.line_total, oi.name_snapshot
FROM order_items oi
WHERE oi.order_id = 1;
```

Replace `1` with your order_id and run.

---

### View Products

```sql
SELECT id, sku, name, price, qty, active, created_at
FROM products
ORDER BY id DESC;
```

---

### Verify Soft Delete

```sql
SELECT id, deleted_at
FROM orders
WHERE deleted_at IS NOT NULL
ORDER BY deleted_at DESC;
```

---

### Check Order Totals

```sql
SELECT id, subtotal, tax_amount, shipping_amount, total_amount
FROM orders
WHERE id = 1;
```

Replace `1` with your order_id.

---

## Quick Checklist for AWS UI Testing

### Step 1: Setup (do once)
- [ ] Note API endpoint from CloudFormation
- [ ] Note user_id from database
- [ ] Note product_ids from database

### Step 2: API Gateway Tests
- [ ] Test GET /products → 200
- [ ] Test GET /products/{id} → 200
- [ ] Test POST /orders → 201 (note order_id)
- [ ] Test GET /orders/{id} → 200
- [ ] Test PUT /orders/{id} → 200
- [ ] Test DELETE /orders/{id} → 200

### Step 3: Lambda Direct Tests
- [ ] Test Orders create event → 201
- [ ] Test Orders get event → 200
- [ ] Test Orders update event → 200
- [ ] Test Orders delete event → 200
- [ ] Test error: invalid product → 400
- [ ] Test error: insufficient stock → 400
- [ ] Test error: missing user_id → 401

### Step 4: Verify Data
- [ ] Check CloudWatch logs (no errors)
- [ ] Query RDS for new orders
- [ ] Confirm deleted_at is set for deleted orders
- [ ] Verify totals are correct

### Step 5: Done
- [ ] All tests passing ✓
- [ ] No errors in logs ✓
- [ ] Data in database correct ✓

---

## Troubleshooting in AWS UI

### If Lambda test fails:

1. Go to **Lambda → Functions → {FunctionName}**
2. Click **Monitor** tab
3. Look at recent invocations in **Metrics**
4. Click **Logs** tab to see CloudWatch logs
5. Search for `[ERROR]` in logs

### If API test shows error:

1. Go to **API Gateway → APIs → ChonkychonkAPI**
2. Click **Logs & Tracing** in left menu
3. Scroll down to see recent API calls
4. Check for any 400/500 errors

### If database query fails:

1. Go to **RDS → Databases → chonky-instance**
2. Check **Monitoring** tab for connection issues
3. Verify security group allows access from Lambda

---

That's it! Copy and paste the JSON/SQL above directly into the AWS console.

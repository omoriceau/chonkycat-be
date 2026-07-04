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

### Test Users GET (List)

**Resource**: `/users`  
**Method**: `GET`

1. Click **Test** button
2. Leave all fields blank (or add query params: `?limit=50&offset=0`)
3. Click **Test**

**Expected Status**: `200`

---

### Test Users by ID GET

**Resource**: `/users/{userId}`  
**Method**: `GET`

1. Click **Test** button
2. **Path Parameters** → `userId`: `1`
3. Click **Test**

**Expected Status**: `200`

---

### Test Users POST (Create)

**Resource**: `/users`  
**Method**: `POST`

1. Click **Test** button
2. **Headers** (add):
   ```
   Content-Type: application/json
   ```
3. **Request Body**:
   ```json
   {
     "email": "john.doe@example.com",
     "first_name": "John",
     "last_name": "Doe",
     "phone": "+1-416-555-0100",
     "role": "customer",
     "status": "active"
   }
   ```
4. Click **Test**

**Expected Status**: `201`  
**Note the returned `user_id`** (e.g., `5`)

---

### Test Users PUT (Update)

**Resource**: `/users/{userId}`  
**Method**: `PUT`

1. Click **Test** button
2. **Headers** (add):
   ```
   Content-Type: application/json
   ```
3. **Path Parameters** → `userId`: `1` (replace with your user_id)
4. **Request Body** (update partial fields):
   ```json
   {
     "first_name": "Jonathan",
     "phone": "+1-416-555-0199"
   }
   ```
5. Click **Test**

**Expected Status**: `200`  
**Response includes**: Updated user data

---

### Test Users DELETE (Soft Delete)

**Resource**: `/users/{userId}`  
**Method**: `DELETE`

1. Click **Test** button
2. **Path Parameters** → `userId`: `1` (replace with your user_id)
3. Click **Test**

**Expected Status**: `200`  
**Response**: `{"message": "User deleted successfully", "user_id": 1}`

---

### Test Users DELETE (Error - User Not Found)

**Resource**: `/users/{userId}`  
**Method**: `DELETE`

1. Click **Test** button
2. **Path Parameters** → `userId`: `99999`
3. Click **Test**

**Expected Status**: `404`  
**Error message**: "User not found"

---

### Test Users GET List with Query Filters

**Resource**: `/users`  
**Method**: `GET`

1. Click **Test** button
2. **Query String Parameters**:
   ```
   limit: 10
   offset: 0
   role: customer
   status: active
   ```
3. Click **Test**

**Expected Status**: `200`  
**Response includes**: Users with applied filters

---

### Test Users POST (Error - Missing Required Fields)

**Resource**: `/users`  
**Method**: `POST`

1. Click **Test** button
2. **Headers** (add):
   ```
   Content-Type: application/json
   ```
3. **Request Body** (missing email):
   ```json
   {
     "first_name": "Jane",
     "last_name": "Smith",
     "role": "customer"
   }
   ```
4. Click **Test**

**Expected Status**: `422`  
**Error message**: "Missing required field: email"

---

### Test Users POST (Error - Invalid Email)

**Resource**: `/users`  
**Method**: `POST`

1. Click **Test** button
2. **Headers** (add):
   ```
   Content-Type: application/json
   ```
3. **Request Body** (invalid email):
   ```json
   {
     "email": "not-an-email",
     "first_name": "Jane",
     "last_name": "Smith",
     "role": "customer",
     "status": "active"
   }
   ```
4. Click **Test**

**Expected Status**: `422`  
**Error message**: "Invalid email format"

---

### Test Users PUT (Error - User Not Found)

**Resource**: `/users/{userId}`  
**Method**: `PUT`

1. Click **Test** button
2. **Headers** (add):
   ```
   Content-Type: application/json
   ```
3. **Path Parameters** → `userId`: `99999`
4. **Request Body**:
   ```json
   {
     "first_name": "Test"
   }
   ```
5. Click **Test**

**Expected Status**: `404`  
**Error message**: "User not found"

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

### Test Orders PUT (Update - Items)

**Resource**: `/orders/{orderId}`  
**Method**: `PUT`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   Content-Type: application/json
   ```
3. **Path Parameters** → `orderId`: `6`
4. **Request Body** (update quantity of product 1):
   ```json
   {
     "items": [
       {"product_id": 1, "quantity": 5}
     ]
   }
   ```
5. Click **Test**

**Expected Status**: `200`  
**Response includes**: Updated subtotal, tax, shipping, total

---

### Test Orders PUT (Update - Shipping)

**Resource**: `/orders/{orderId}`  
**Method**: `PUT`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   Content-Type: application/json
   ```
3. **Path Parameters** → `orderId`: `6`
4. **Request Body** (update shipping address):
   ```json
   {
     "shipping": {
       "name": "Jane Smith",
       "address1": "789 Elm Street",
       "city": "Montreal",
       "province": "QC",
       "postal_code": "H1A 1A1",
       "country": "Canada"
     }
   }
   ```
5. Click **Test**

**Expected Status**: `200`

---

### Test Orders PUT (Update - Notes)

**Resource**: `/orders/{orderId}`  
**Method**: `PUT`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   Content-Type: application/json
   ```
3. **Path Parameters** → `orderId`: `6`
4. **Request Body** (update customer notes):
   ```json
   {
     "customer_notes": "Call before delivery, ring doorbell twice"
   }
   ```
5. Click **Test**

**Expected Status**: `200`

---

### Test Orders PUT (Update - Promotion Code)

**Resource**: `/orders/{orderId}`  
**Method**: `PUT`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   Content-Type: application/json
   ```
3. **Path Parameters** → `orderId`: `6`
4. **Request Body** (apply promotion code):
   ```json
   {
     "promotion_code": "WELCOME10"
   }
   ```
5. Click **Test**

**Expected Status**: `200`  
**Response includes**: Updated discount, tax, total

---

### Test Orders PUT (Update - Multiple Fields)

**Resource**: `/orders/{orderId}`  
**Method**: `PUT`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   Content-Type: application/json
   ```
3. **Path Parameters** → `orderId`: `6`
4. **Request Body** (update items and notes):
   ```json
   {
     "items": [
       {"product_id": 1, "quantity": 2},
       {"product_id": 2, "quantity": 1}
     ],
     "customer_notes": "Please leave at door"
   }
   ```
5. Click **Test**

**Expected Status**: `200`

---

### Test Orders PUT (Error - Order Not Found)

**Resource**: `/orders/{orderId}`  
**Method**: `PUT`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   Content-Type: application/json
   ```
3. **Path Parameters** → `orderId`: `99999`
4. **Request Body**:
   ```json
   {
     "customer_notes": "Test note"
   }
   ```
5. Click **Test**

**Expected Status**: `404`  
**Error message**: "Order not found"

---

### Test Orders PUT (Error - Invalid Product in Items)

**Resource**: `/orders/{orderId}`  
**Method**: `PUT`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   Content-Type: application/json
   ```
3. **Path Parameters** → `orderId`: `6`
4. **Request Body**:
   ```json
   {
     "items": [
       {"product_id": 99999, "quantity": 1}
     ]
   }
   ```
5. Click **Test**

**Expected Status**: `422`  
**Error message**: "Product 99999 not found"

---

### Test Orders PUT (Error - Insufficient Stock)

**Resource**: `/orders/{orderId}`  
**Method**: `PUT`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   Content-Type: application/json
   ```
3. **Path Parameters** → `orderId`: `6`
4. **Request Body** (request more than available):
   ```json
   {
     "items": [
       {"product_id": 1, "quantity": 10000}
     ]
   }
   ```
5. Click **Test**

**Expected Status**: `422`  
**Error message**: "Insufficient stock for..." 

---

### Test Orders PUT (Error - No Update Fields)

**Resource**: `/orders/{orderId}`  
**Method**: `PUT`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   Content-Type: application/json
   ```
3. **Path Parameters** → `orderId`: `6`
4. **Request Body** (empty):
   ```json
   {}
   ```
5. Click **Test**

**Expected Status**: `422`  
**Error message**: "At least one field must be provided for update..."

---

### Test Orders DELETE (Soft Delete)

**Resource**: `/orders/{orderId}`  
**Method**: `DELETE`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   ```
3. **Path Parameters** → `orderId`: `6`
4. Click **Test**

**Expected Status**: `200`  
**Response**: `{"message": "Order deleted successfully", "order_id": 6}`

---

### Test Orders DELETE (Error - Order Not Found)

**Resource**: `/orders/{orderId}`  
**Method**: `DELETE`

1. Click **Test** button
2. **Headers** (add):
   ```
   X-User-Id: 1
   ```
3. **Path Parameters** → `orderId`: `99999`
4. Click **Test**

**Expected Status**: `404`  
**Error message**: "Order not found"

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

### Test Orders Function - Update Order (Items Only)

**Function**: `chonkychonk-orders-dev`  
**Event name**: `update-order-items`

1. Click **Create new event**
2. **Event name**: `update-order-items`
3. Replace with:

```json
{
  "httpMethod": "PUT",
  "path": "/orders/6",
  "pathParameters": {
    "orderId": "6"
  },
  "headers": {
    "X-User-Id": "1",
    "Content-Type": "application/json"
  },
  "body": "{\"items\": [{\"product_id\": 1, \"quantity\": 5}]}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, updated totals in response

---

### Test Orders Function - Update Order (Shipping)

**Function**: `chonkychonk-orders-dev`  
**Event name**: `update-order-shipping`

1. Click **Create new event**
2. **Event name**: `update-order-shipping`
3. Replace with:

```json
{
  "httpMethod": "PUT",
  "path": "/orders/6",
  "pathParameters": {
    "orderId": "6"
  },
  "headers": {
    "X-User-Id": "1",
    "Content-Type": "application/json"
  },
  "body": "{\"shipping\": {\"name\": \"Jane Smith\", \"address1\": \"789 Elm St\", \"city\": \"Montreal\", \"province\": \"QC\", \"postal_code\": \"H1A 1A1\", \"country\": \"Canada\"}}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, updated shipping in response

---

### Test Orders Function - Update Order (Notes)

**Function**: `chonkychonk-orders-dev`  
**Event name**: `update-order-notes`

1. Click **Create new event**
2. **Event name**: `update-order-notes`
3. Replace with:

```json
{
  "httpMethod": "PUT",
  "path": "/orders/6",
  "pathParameters": {
    "orderId": "6"
  },
  "headers": {
    "X-User-Id": "1",
    "Content-Type": "application/json"
  },
  "body": "{\"customer_notes\": \"Call before delivery\"}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, updated notes in response

---

### Test Orders Function - Update Order (Promotion)

**Function**: `chonkychonk-orders-dev`  
**Event name**: `update-order-promo`

1. Click **Create new event**
2. **Event name**: `update-order-promo`
3. Replace with:

```json
{
  "httpMethod": "PUT",
  "path": "/orders/6",
  "pathParameters": {
    "orderId": "6"
  },
  "headers": {
    "X-User-Id": "1",
    "Content-Type": "application/json"
  },
  "body": "{\"promotion_code\": \"WELCOME10\"}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, discount applied in response

---

### Test Orders Function - Update Order (Error - Not Found)

**Function**: `chonkychonk-orders-dev`  
**Event name**: `error-update-not-found`

1. Click **Create new event**
2. **Event name**: `error-update-not-found`
3. Replace with:

```json
{
  "httpMethod": "PUT",
  "path": "/orders/99999",
  "pathParameters": {
    "orderId": "99999"
  },
  "headers": {
    "X-User-Id": "1",
    "Content-Type": "application/json"
  },
  "body": "{\"customer_notes\": \"test\"}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 404, "Order not found"

---

### Test Orders Function - Update Order (Error - Invalid Product)

**Function**: `chonkychonk-orders-dev`  
**Event name**: `error-update-invalid-product`

1. Click **Create new event**
2. **Event name**: `error-update-invalid-product`
3. Replace with:

```json
{
  "httpMethod": "PUT",
  "path": "/orders/6",
  "pathParameters": {
    "orderId": "6"
  },
  "headers": {
    "X-User-Id": "1",
    "Content-Type": "application/json"
  },
  "body": "{\"items\": [{\"product_id\": 99999, \"quantity\": 1}]}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 422, "Product 99999 not found"

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
  "path": "/orders/6",
  "pathParameters": {
    "orderId": "6"
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

**Expected**: Status 200, "Order deleted successfully"

---

### Test Orders Function - Delete Order (Error - Not Found)

**Function**: `chonkychonk-orders-dev`  
**Event name**: `error-delete-not-found`

1. Click **Create new event**
2. **Event name**: `error-delete-not-found`
3. Replace with:

```json
{
  "httpMethod": "DELETE",
  "path": "/orders/99999",
  "pathParameters": {
    "orderId": "99999"
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

**Expected**: Status 404, "Order not found"

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

### Test Users Function - List Users

**Function**: `chonkychonk-users-dev`  
**Event name**: `list-users`

1. Click **Create new event**
2. **Event name**: `list-users`
3. Replace with:

```json
{
  "httpMethod": "GET",
  "path": "/users",
  "queryStringParameters": {
    "limit": "50",
    "offset": "0"
  },
  "headers": {},
  "body": null,
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, users list in response

---

### Test Users Function - Get Single User

**Function**: `chonkychonk-users-dev`  
**Event name**: `get-user`

1. Click **Create new event**
2. **Event name**: `get-user`
3. Replace with:

```json
{
  "httpMethod": "GET",
  "path": "/users/1",
  "pathParameters": {
    "userId": "1"
  },
  "headers": {},
  "body": null,
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, user data in response

---

### Test Users Function - Create User

**Function**: `chonkychonk-users-dev`  
**Event name**: `create-user`

1. Click **Create new event**
2. **Event name**: `create-user`
3. Replace with:

```json
{
  "httpMethod": "POST",
  "path": "/users",
  "headers": {
    "Content-Type": "application/json"
  },
  "body": "{\"email\": \"alice.wonder@example.com\", \"first_name\": \"Alice\", \"last_name\": \"Wonder\", \"phone\": \"+1-416-555-0123\", \"role\": \"customer\", \"status\": \"active\"}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 201, new user_id in response

---

### Test Users Function - Update User

**Function**: `chonkychonk-users-dev`  
**Event name**: `update-user`

1. Click **Create new event**
2. **Event name**: `update-user`
3. Replace with:

```json
{
  "httpMethod": "PUT",
  "path": "/users/1",
  "pathParameters": {
    "userId": "1"
  },
  "headers": {
    "Content-Type": "application/json"
  },
  "body": "{\"first_name\": \"Jonathan\", \"phone\": \"+1-416-555-0199\"}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, updated user data in response

---

### Test Users Function - Delete User

**Function**: `chonkychonk-users-dev`  
**Event name**: `delete-user`

1. Click **Create new event**
2. **Event name**: `delete-user`
3. Replace with:

```json
{
  "httpMethod": "DELETE",
  "path": "/users/1",
  "pathParameters": {
    "userId": "1"
  },
  "headers": {},
  "body": null,
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 200, "User deleted successfully"

---

### Test Users Function - Error Case (User Not Found - GET)

**Function**: `chonkychonk-users-dev`  
**Event name**: `error-user-not-found-get`

1. Click **Create new event**
2. **Event name**: `error-user-not-found-get`
3. Replace with:

```json
{
  "httpMethod": "GET",
  "path": "/users/99999",
  "pathParameters": {
    "userId": "99999"
  },
  "headers": {},
  "body": null,
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 404, "User not found"

---

### Test Users Function - Error Case (User Not Found - PUT)

**Function**: `chonkychonk-users-dev`  
**Event name**: `error-user-not-found-put`

1. Click **Create new event**
2. **Event name**: `error-user-not-found-put`
3. Replace with:

```json
{
  "httpMethod": "PUT",
  "path": "/users/99999",
  "pathParameters": {
    "userId": "99999"
  },
  "headers": {
    "Content-Type": "application/json"
  },
  "body": "{\"first_name\": \"Test\"}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 404, "User not found"

---

### Test Users Function - Error Case (User Not Found - DELETE)

**Function**: `chonkychonk-users-dev`  
**Event name**: `error-user-not-found-delete`

1. Click **Create new event**
2. **Event name**: `error-user-not-found-delete`
3. Replace with:

```json
{
  "httpMethod": "DELETE",
  "path": "/users/99999",
  "pathParameters": {
    "userId": "99999"
  },
  "headers": {},
  "body": null,
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 404, "User not found"

---

### Test Users Function - Error Case (Missing Required Field)

**Function**: `chonkychonk-users-dev`  
**Event name**: `error-missing-field`

1. Click **Create new event**
2. **Event name**: `error-missing-field`
3. Replace with:

```json
{
  "httpMethod": "POST",
  "path": "/users",
  "headers": {
    "Content-Type": "application/json"
  },
  "body": "{\"first_name\": \"Jane\", \"last_name\": \"Smith\", \"role\": \"customer\"}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 422, error message about missing required field

---

### Test Users Function - Error Case (Invalid Email)

**Function**: `chonkychonk-users-dev`  
**Event name**: `error-invalid-email`

1. Click **Create new event**
2. **Event name**: `error-invalid-email`
3. Replace with:

```json
{
  "httpMethod": "POST",
  "path": "/users",
  "headers": {
    "Content-Type": "application/json"
  },
  "body": "{\"email\": \"not-an-email\", \"first_name\": \"Jane\", \"last_name\": \"Smith\", \"role\": \"customer\", \"status\": \"active\"}",
  "isBase64Encoded": false
}
```

4. Click **Save**
5. Click **Test**

**Expected**: Status 422, error message about invalid email

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

### View Users Lambda Logs

1. Search: `/aws/lambda/chonkychonk-users-dev`
2. Click the log group
3. View recent log streams
4. Look for `[DEBUG]`, `[ERROR]`, `[INFO]` entries

---

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

### View Recent Users

```sql
SELECT id, email, first_name, last_name, phone, role, status, created_at, deleted_at
FROM users
ORDER BY created_at DESC
LIMIT 10;
```

Copy and paste this into Query Editor and run.

---

### View Users by Role

```sql
SELECT id, email, first_name, last_name, role, status, created_at
FROM users
WHERE role = 'customer'
ORDER BY created_at DESC
LIMIT 20;
```

Replace `'customer'` with `'admin'` or `'manager'` to view different roles.

---

### Verify User Soft Delete

```sql
SELECT id, email, first_name, last_name, deleted_at
FROM users
WHERE deleted_at IS NOT NULL
ORDER BY deleted_at DESC;
```

---

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
- [ ] Test GET /users → 200
- [ ] Test GET /users/{id} → 200
- [ ] Test POST /users → 201 (note user_id)
- [ ] Test PUT /users/{id} → 200
- [ ] Test DELETE /users/{id} → 200
- [ ] Test GET /products → 200
- [ ] Test GET /products/{id} → 200
- [ ] Test POST /orders → 201 (note order_id)
- [ ] Test GET /orders/{id} → 200
- [ ] Test PUT /orders/{id} items → 200
- [ ] Test PUT /orders/{id} shipping → 200
- [ ] Test PUT /orders/{id} notes → 200
- [ ] Test PUT /orders/{id} promotion → 200
- [ ] Test PUT /orders/{id} multiple fields → 200
- [ ] Test DELETE /orders/{id} → 200

### Step 3: Lambda Direct Tests
- [ ] Test Users list event → 200
- [ ] Test Users get event → 200
- [ ] Test Users create event → 201
- [ ] Test Users update event → 200
- [ ] Test Users delete event → 200
- [ ] Test error: user not found (GET) → 404
- [ ] Test error: user not found (PUT) → 404
- [ ] Test error: user not found (DELETE) → 404
- [ ] Test error: missing required field → 422
- [ ] Test error: invalid email → 422
- [ ] Test Orders create event → 201
- [ ] Test Orders get event → 200
- [ ] Test Orders update items event → 200
- [ ] Test Orders update shipping event → 200
- [ ] Test Orders update notes event → 200
- [ ] Test Orders update promo event → 200
- [ ] Test Orders delete event → 200
- [ ] Test error: order not found (GET) → 404
- [ ] Test error: order not found (PUT) → 404
- [ ] Test error: order not found (DELETE) → 404
- [ ] Test error: invalid product in items → 422
- [ ] Test error: insufficient stock → 422
- [ ] Test error: invalid product in update → 422
- [ ] Test error: missing update fields → 422

### Step 4: Verify Data
- [ ] Check CloudWatch logs (no errors)
- [ ] Query RDS for new users
- [ ] Query RDS for new orders
- [ ] Confirm deleted_at is set for deleted users
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

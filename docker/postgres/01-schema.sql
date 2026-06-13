CREATE TABLE public.users (
  id BIGSERIAL PRIMARY KEY,
  email VARCHAR(255) NOT NULL UNIQUE,
  first_name VARCHAR(100),
  last_name VARCHAR(100),
  phone VARCHAR(50),
  role VARCHAR(50) NOT NULL DEFAULT 'customer',
  status VARCHAR(50) NOT NULL DEFAULT 'active',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE public.products (
  id BIGSERIAL PRIMARY KEY,
  sku VARCHAR(100) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  ingredients TEXT,
  image_url VARCHAR(255),
  category VARCHAR(100),
  price DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  qty INT NOT NULL DEFAULT 0,
  low_stock_threshold INT NOT NULL DEFAULT 10,
  active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE public.orders (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  status VARCHAR(50) NOT NULL DEFAULT 'pending',
  subtotal DECIMAL(10,2) NOT NULL,
  tax_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  shipping_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  total_amount DECIMAL(10,2) NOT NULL,
  customer_notes TEXT,
  shipping_name VARCHAR(255),
  shipping_address1 VARCHAR(255),
  shipping_address2 VARCHAR(255),
  shipping_city VARCHAR(100),
  shipping_province VARCHAR(100),
  shipping_postal_code VARCHAR(20),
  shipping_country VARCHAR(100),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES public.users (id)
);

CREATE TABLE public.order_items (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL,
  product_id BIGINT NOT NULL,
  quantity INT NOT NULL,
  unit_price DECIMAL(10,2) NOT NULL,
  line_total DECIMAL(10,2) NOT NULL,
  name_snapshot VARCHAR(255),
  FOREIGN KEY (order_id) REFERENCES public.orders (id),
  FOREIGN KEY (product_id) REFERENCES public.products (id)
);

CREATE TABLE public.payments (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL,
  payment_provider VARCHAR(100),
  provider_transaction_id VARCHAR(255),
  method VARCHAR(50),
  amount DECIMAL(10,2) NOT NULL,
  currency CHAR(3) NOT NULL DEFAULT 'CAD',
  status VARCHAR(50) NOT NULL,
  paid_at TIMESTAMP NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (order_id) REFERENCES public.orders (id)
);

CREATE TABLE public.refunds (
  id BIGSERIAL PRIMARY KEY,
  payment_id BIGINT NOT NULL,
  amount DECIMAL(10,2) NOT NULL,
  reason TEXT,
  status VARCHAR(50) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (payment_id) REFERENCES public.payments (id)
);

CREATE TABLE public.promotions (
  id BIGSERIAL PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE,
  discount_type VARCHAR(20) NOT NULL,
  discount_value DECIMAL(10,2) NOT NULL,
  active BOOLEAN NOT NULL DEFAULT true,
  expires_at TIMESTAMP NULL,
  deactivated_at TIMESTAMP NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE public.order_promotions (
  order_id BIGINT NOT NULL,
  promotion_id BIGINT NOT NULL,
  discount_amount DECIMAL(10,2) NOT NULL,
  PRIMARY KEY (order_id, promotion_id),
  FOREIGN KEY (order_id) REFERENCES public.orders (id),
  FOREIGN KEY (promotion_id) REFERENCES public.promotions (id)
);

CREATE TABLE public.order_tracking (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL,
  carrier VARCHAR(100) NOT NULL,
  tracking_number VARCHAR(255) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (order_id) REFERENCES public.orders (id)
);

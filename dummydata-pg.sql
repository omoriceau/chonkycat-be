-- PostgreSQL version of dummydata.sql
-- Converted from MySQL syntax

-- ============================================================
-- PRODUCTS — Cat Food for Fat Cats
-- ============================================================
INSERT INTO products
  (sku, name, description, image_url, category, price, qty, low_stock_threshold, active)
VALUES

  -- -------------------------------------------------------
  -- DRY FOOD (2 product lines, 3 SKUs each)
  -- -------------------------------------------------------

  -- Chonky Kibble line
  ('DRY-CK-001', 'Chonky Kibble – Original Rotisserie Chicken',
   'Our flagship dry food. Slow-roasted chicken flavour with taurine-fortified crunchies sized for the discerning chonk. High protein, zero apologies.',
   'img/dry-ck-001.jpg', 'Dry Food', 22.99, 200, 25, true),

  ('DRY-CK-002', 'Chonky Kibble – Salmon & Sweet Potato',
   'Wild-caught salmon paired with sweet potato for a carb-forward crunch your fluffy couch potato deserves. Omega-3 enriched for a shiny, majestic coat.',
   'img/dry-ck-002.jpg', 'Dry Food', 23.99, 175, 25, true),

  ('DRY-CK-003', 'Chonky Kibble – Beef & Brown Rice',
   'Hearty grass-fed beef kibble with whole-grain brown rice. The dinner-table energy your cat has always wanted but could never reach from the floor.',
   'img/dry-ck-003.jpg', 'Dry Food', 22.99, 160, 25, true),

  -- Chonk+ Kibble line (premium / functional)
  ('DRY-CP-001', 'Chonk+ Kibble – Joint Support Turkey & Glucosamine',
   'Premium turkey kibble fortified with glucosamine and chondroitin. For the senior chonk who still insists on jumping onto the counter despite all medical advice.',
   'img/dry-cp-001.jpg', 'Dry Food', 31.99, 120, 20, true),

  ('DRY-CP-002', 'Chonk+ Kibble – Gravy-Soaked Double Chicken with Beef Drippings',
   'Two kinds of chicken — roasted AND fried — bathed in rendered beef drippings and pressure-sealed into every kibble. Veterinarian-disapproved. Cat-adored. The Chonk+ line is about thriving, not surviving. Your cat did not come to this earth to eat diet food and you know it.',
   'img/dry-cp-002.jpg', 'Dry Food', 31.99, 90, 20, true),

  ('DRY-CP-003', 'Chonk+ Kibble – Hairball Control Duck & Oat',
   'Novel-protein duck with whole oat and psyllium husk for a digestive system that deserves some peace and quiet. Your carpet will thank you.',
   'img/dry-cp-003.jpg', 'Dry Food', 33.99, 8, 20, true),


  -- -------------------------------------------------------
  -- WET FOOD — Pâtés & Stews (imaginative small-animal flavours)
  -- -------------------------------------------------------

  ('WET-001', 'Rustic Sparrow Pâté with Roasted Seed Jus',
   'Free-range garden sparrow slow-blended into a silky pâté, finished with a reduction of sunflower seed jus and a whisper of thyme. Très chic. Very chonk.',
   'img/wet-001.jpg', 'Wet Food', 4.49, 300, 40, true),

  ('WET-002', 'Meadow Vole Minced in Clover Broth',
   'Tender meadow vole, hand-minced and simmered in a fragrant wild-clover broth with heirloom carrot ribbons. The countryside, in a tin.',
   'img/wet-002.jpg', 'Wet Food', 4.49, 280, 40, true),

  ('WET-003', 'Cornish Pigeon Confit with Giblet Gravy',
   'Heritage-breed pigeon slow-cooked confit-style until fall-apart tender, served in its own rich giblet gravy. A dish your cat will inhale in 11 seconds flat.',
   'img/wet-003.jpg', 'Wet Food', 4.99, 260, 40, true),

  ('WET-004', 'Garter Snake Terrine with Forest Mushroom',
   'Delicately deboned garter snake pressed into a smooth terrine with foraged forest mushrooms and a hint of smoked paprika. Polarising at dinner parties. Beloved at breakfast.',
   'img/wet-004.jpg', 'Wet Food', 4.99, 220, 40, true),

  ('WET-005', 'Chipmunk Fricassée with Acorn Butter Sauce',
   'Braised chipmunk in a classic fricassée with pearl onions, green beans, and a velvety acorn butter sauce. French technique. Canadian chipmunk. Universal approval.',
   'img/wet-005.jpg', 'Wet Food', 5.29, 190, 35, true),

  ('WET-006', 'Field Mouse Bourguignon',
   'Slow-braised field mouse in a robust Burgundy-style reduction with lardons, button mushrooms, and baby carrots. Oui oui. Nom nom.',
   'img/wet-006.jpg', 'Wet Food', 5.29, 210, 35, true),

  ('WET-007', 'Song Thrush & Worm Medley Pâté',
   'Earthy song thrush blended with earthworm protein into a deeply savoury pâté. Rich in iron. Richer in personality. Not for the faint of heart — but your cat is not faint of heart.',
   'img/wet-007.jpg', 'Wet Food', 4.79, 240, 40, true),

  ('WET-008', 'Baby Rabbit Stew with Tarragon & Marrow',
   'Meltingly tender rabbit braised with fresh tarragon and bone marrow for an unctuous, collagen-rich broth. The Sunday roast your cat has been silently judging you for not making.',
   'img/wet-008.jpg', 'Wet Food', 5.49, 170, 30, true),

  ('WET-009', 'Shrew Tartare with Quail Egg Crumble',
   'Finely chopped common shrew seasoned with a hint of Dijon and capers, crowned with a crumble of dried quail egg yolk. Raw. Refined. Completely unhinged.',
   'img/wet-009.jpg', 'Wet Food', 5.49, 6, 30, true),

  ('WET-010', 'Roasted Finch Pâté with Sun-Dried Cricket Crust',
   'Garden finch roasted and blended to a smooth pâté, encrusted with sun-dried crickets for an artisanal crunch. High in sustainable protein. Low in your cat''s gratitude.',
   'img/wet-010.jpg', 'Wet Food', 5.29, 200, 35, true),


  -- -------------------------------------------------------
  -- SNACKS
  -- -------------------------------------------------------

  ('SNK-001', 'Freeze-Dried Minnow Crisps',
   'Whole river minnows freeze-dried at peak freshness into an ultra-crunchy crisp. One ingredient. Infinite chaos. Shake the bag from across the house and watch a 14 lb cat achieve liftoff.',
   'img/snk-001.jpg', 'Snacks', 11.99, 150, 20, true),

  ('SNK-002', 'Chonky Chonk Pill Pockets – Chicken Flavour',
   'Soft, mouldable chicken-flavoured pouches for hiding medication inside. Works great until your cat eats around the pill with surgical precision and stares at you.',
   'img/snk-002.jpg', 'Snacks', 13.99, 130, 20, true),

  ('SNK-003', 'Lickable Tuna & Pumpkin Squeeze Tubes (6-pack)',
   'Six single-serve squeeze tubes of creamy tuna and pumpkin purée. High in moisture. Excellent for bonding, bribery, and basic veterinary compliance.',
   'img/snk-003.jpg', 'Snacks', 9.99, 220, 30, true),

  ('SNK-004', 'Crunchy Locust & Catnip Nuggets',
   'Whole roasted locusts tumbled in premium dried catnip and shaped into paw-sized nuggets. Sustainably farmed. Spiritually chaotic. You''ve been warned.',
   'img/snk-004.jpg', 'Snacks', 10.99, 9, 15, true),

  ('SNK-005', 'Smoked Anchovy Dental Chews',
   'Chewy anchovy-flavoured strips with an abrasive texture designed to support dental health. Your cat will pretend to hate them while finishing the bag in one sitting.',
   'img/snk-005.jpg', 'Snacks', 12.99, 110, 20, true),

  ('SNK-006', 'Duck Liver Mousse Bites',
   'Airy, melt-in-mouth duck liver mousse set into bite-sized cubes and freeze-dried. The amuse-bouche your cat deserves after a long day of napping on your laptop.',
   'img/snk-006.jpg', 'Snacks', 14.99, 75, 15, true);

-- ============================================================
-- USERS  (1 admin, 3 registered customers, 2 guests)
-- ============================================================
INSERT INTO users
  (email, first_name, last_name, phone, role, status)
VALUES
  -- Admin
  ('admin@chonkychonk.com',  'Aria',   'Nakamura', '416-555-0100', 'admin',    'active'),
  -- Registered customers
  ('benny.garcia@email.com', 'Benny',  'Garcia',   '647-555-0201', 'customer', 'active'),
  ('priya.nair@email.com',   'Priya',  'Nair',     '416-555-0302', 'customer', 'active'),
  ('tom.okafor@email.com',   'Tom',    'Okafor',   '905-555-0403', 'customer', 'active'),
  -- Guests (no name/phone — identified by email only)
  ('guest.alice@email.com',  NULL,     NULL,        NULL,          'guest',    'active'),
  ('guest.bob@email.com',    NULL,     NULL,        NULL,          'guest',    'active');


-- ============================================================
-- PROMOTIONS
-- ============================================================
INSERT INTO promotions
  (code, discount_type, discount_value, active, expires_at)
VALUES
  ('WELCOME10',  'percentage', 10.00, true, '2026-12-31 23:59:59'),
  ('FLAT5OFF',   'fixed',       5.00, true, '2026-09-30 23:59:59'),
  ('SUMMER20',   'percentage', 20.00, false, '2025-08-31 23:59:59');

-- ============================================================
-- ORDERS + ITEMS + PAYMENTS + TRACKING + REFUNDS
-- ============================================================

-- --------------------------------------------------
-- ORDER 1: Benny — completed, paid, shipped
-- --------------------------------------------------
INSERT INTO orders
  (user_id, status, subtotal, tax_amount, shipping_amount, total_amount,
   shipping_name, shipping_address1, shipping_city, shipping_province,
   shipping_postal_code, shipping_country)
VALUES
  (2, 'completed', 109.97, 14.30, 10.00, 134.27,
   'Benny Garcia', '42 Maple Ave', 'Toronto', 'ON', 'M5V 2H1', 'Canada');

INSERT INTO order_items
  (order_id, product_id, quantity, unit_price, line_total, name_snapshot)
VALUES
  (1, 1, 1, 34.99,  34.99, 'Chonky Logo Tee'),
  (1, 2, 1, 74.99,  74.99, 'Oversized Hoodie');

INSERT INTO payments
  (order_id, payment_provider, provider_transaction_id, method,
   amount, currency, status, paid_at)
VALUES
  (1, 'Stripe', 'ch_3PaA1BCdef001', 'credit_card', 134.27, 'CAD', 'paid', '2025-11-02 10:15:00');

INSERT INTO order_tracking
  (order_id, carrier, tracking_number)
VALUES
  (1, 'Canada Post', 'CP123456789CA');


-- --------------------------------------------------
-- ORDER 2: Priya — completed, paid, promo applied
-- --------------------------------------------------
INSERT INTO orders
  (user_id, status, subtotal, tax_amount, shipping_amount, total_amount,
   shipping_name, shipping_address1, shipping_city, shipping_province,
   shipping_postal_code, shipping_country)
VALUES
  (3, 'completed', 54.97, 7.15, 0.00, 57.97,
   'Priya Nair', '88 Queen St W', 'Toronto', 'ON', 'M5H 2N2', 'Canada');

INSERT INTO order_items
  (order_id, product_id, quantity, unit_price, line_total, name_snapshot)
VALUES
  (2, 4, 2, 14.99, 29.98, 'Enamel Pin Set (3-pack)'),
  (2, 7, 1, 19.99, 19.99, '16 oz Ceramic Mug'),
  (2, 3, 1, 29.99, 29.99, 'Chonk Dad Hat');

INSERT INTO order_promotions
  (order_id, promotion_id, discount_amount)
VALUES
  (2, 2, 5.00);

INSERT INTO payments
  (order_id, payment_provider, provider_transaction_id, method,
   amount, currency, status, paid_at)
VALUES
  (2, 'Stripe', 'ch_3PaA2BCdef002', 'credit_card', 57.97, 'CAD', 'paid', '2025-11-15 14:32:00');

INSERT INTO order_tracking
  (order_id, carrier, tracking_number)
VALUES
  (2, 'Purolator', 'PUR987654321');


-- --------------------------------------------------
-- ORDER 3: Tom — refunded after payment
-- --------------------------------------------------
INSERT INTO orders
  (user_id, status, subtotal, tax_amount, shipping_amount, total_amount,
   shipping_name, shipping_address1, shipping_city, shipping_province,
   shipping_postal_code, shipping_country)
VALUES
  (4, 'refunded', 74.99, 9.75, 10.00, 94.74,
   'Tom Okafor', '10 Bay St', 'Hamilton', 'ON', 'L8P 1A1', 'Canada');

INSERT INTO order_items
  (order_id, product_id, quantity, unit_price, line_total, name_snapshot)
VALUES
  (3, 2, 1, 74.99, 74.99, 'Oversized Hoodie');

INSERT INTO payments
  (order_id, payment_provider, provider_transaction_id, method,
   amount, currency, status, paid_at)
VALUES
  (3, 'Stripe', 'ch_3PaA3BCdef003', 'credit_card', 94.74, 'CAD', 'paid', '2025-12-01 09:00:00');

INSERT INTO refunds
  (payment_id, amount, reason, status)
VALUES
  (3, 94.74, 'Customer changed mind — returned unopened.', 'refunded');


-- --------------------------------------------------
-- ORDER 4: Guest Alice — pending payment
-- --------------------------------------------------
INSERT INTO orders
  (user_id, status, subtotal, tax_amount, shipping_amount, total_amount,
   customer_notes,
   shipping_name, shipping_address1, shipping_city, shipping_province,
   shipping_postal_code, shipping_country)
VALUES
  (5, 'pending', 34.98, 4.55, 10.00, 49.53,
   'Please leave at door.',
   'Alice Lam', '300 Front St W', 'Toronto', 'ON', 'M5V 0E9', 'Canada');

INSERT INTO order_items
  (order_id, product_id, quantity, unit_price, line_total, name_snapshot)
VALUES
  (4, 3, 1, 29.99, 29.99, 'Chonk Dad Hat'),
  (4, 6, 1,  9.99,  9.99, 'Sticker Sheet');

INSERT INTO payments
  (order_id, payment_provider, provider_transaction_id, method,
   amount, currency, status)
VALUES
  (4, 'Stripe', 'pi_3PaA4BCdef004', 'credit_card', 49.53, 'CAD', 'pending');


-- --------------------------------------------------
-- ORDER 5: Guest Bob — completed, paid, promo + shipped
-- --------------------------------------------------
INSERT INTO orders
  (user_id, status, subtotal, tax_amount, shipping_amount, total_amount,
   shipping_name, shipping_address1, shipping_city, shipping_province,
   shipping_postal_code, shipping_country)
VALUES
  (6, 'completed', 89.97, 10.52, 0.00, 91.49,
   'Bob Tremblay', '1 Rideau St', 'Ottawa', 'ON', 'K1N 8S7', 'Canada');

INSERT INTO order_items
  (order_id, product_id, quantity, unit_price, line_total, name_snapshot)
VALUES
  (5, 8,  1, 39.99, 39.99, '20 oz Insulated Tumbler'),
  (5, 10, 1, 24.99, 24.99, '8×10 Art Print – "Cloud Nine"'),
  (5, 11, 1, 49.99, 49.99, '12×16 Art Print – "Chonk City"');

INSERT INTO order_promotions
  (order_id, promotion_id, discount_amount)
VALUES
  (5, 1, 9.00);

INSERT INTO payments
  (order_id, payment_provider, provider_transaction_id, method,
   amount, currency, status, paid_at)
VALUES
  (5, 'Stripe', 'ch_3PaA5BCdef005', 'paypal', 91.49, 'CAD', 'paid', '2026-01-10 18:44:00');

INSERT INTO order_tracking
  (order_id, carrier, tracking_number)
VALUES
  (5, 'FedEx', 'FX112233445566');

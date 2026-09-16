-- Gold views for the Supply Chain + ERP/Orders plane, per the
-- medallion-layers design memo (gold_inventory_position,
-- gold_order_fulfillment_kpi, gold_supplier_scorecard). Views, not
-- materialized/indexed views -- this data is small and batch-loaded
-- (one seed run, not continuously streamed), so a plain view recomputes
-- cheaply on every query with no separate refresh step to manage.
--
-- Run 01_tables.sql (+ its seed data) first.

CREATE VIEW gold_inventory_position AS
SELECT
    i.FactoryId,
    m.Type AS MaterialType,
    i.QuantityOnHand,
    i.ReorderLevel,
    CASE WHEN i.QuantityOnHand <= i.ReorderLevel THEN 1 ELSE 0 END AS StockoutRisk,
    i.LastUpdated
FROM inventory i
JOIN material m ON m.MaterialId = i.MaterialId
GO

CREATE VIEW gold_supplier_scorecard AS
SELECT
    s.SupplierId,
    s.Name,
    s.MaterialType,
    s.Country,
    s.Rating,
    COUNT(m.MaterialId) AS LotCount,
    SUM(m.QuantityKg) AS TotalQuantityKg,
    MAX(m.ReceivedDate) AS LastReceivedDate
FROM supplier s
LEFT JOIN material m ON m.SupplierId = s.SupplierId
GROUP BY s.SupplierId, s.Name, s.MaterialType, s.Country, s.Rating
GO

-- PaymentStatus: NotInvoiced (order not yet invoiced, e.g. still Draft),
-- Paid, Overdue (past DueDate, unpaid -- the figure that matters for
-- cash-flow questions per demo/kb/03-erp-orders.md, not just "unpaid
-- count"), or Outstanding (not yet due).
CREATE VIEW gold_order_fulfillment_kpi AS
SELECT
    o.OrderId,
    o.CustomerId,
    c.Segment AS CustomerSegment,
    o.OrderDate,
    o.Status AS OrderStatus,
    o.TotalAmount,
    o.Currency,
    inv.DueDate,
    inv.AmountDue,
    inv.PaidDate,
    CASE
        WHEN inv.InvoiceId IS NULL THEN 'NotInvoiced'
        WHEN inv.PaidDate IS NOT NULL THEN 'Paid'
        WHEN inv.DueDate < CAST(GETUTCDATE() AS DATE) THEN 'Overdue'
        ELSE 'Outstanding'
    END AS PaymentStatus
FROM sales_order o
JOIN customer c ON c.CustomerId = o.CustomerId
LEFT JOIN invoice inv ON inv.OrderId = o.OrderId
GO

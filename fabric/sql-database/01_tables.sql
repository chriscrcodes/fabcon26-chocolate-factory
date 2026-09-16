-- Supply Chain + ERP/Orders tables for the chocolate factory Fabric SQL
-- Database. Batch/transactional business data -- the other plane from
-- the Eventhouse (Factory/Quality telemetry), per the medallion-layers
-- design memo's two-plane design. Column names/types match
-- demo/ontology/ontology_config.json exactly.
--
-- Run this first, via demo/sql-database/deploy_sql_database.py. Each
-- statement is its own GO batch (CREATE TABLE doesn't strictly require
-- this, but it keeps the script consistent with 02_gold.sql, where
-- CREATE VIEW does require it).

CREATE TABLE supplier (
    SupplierId NVARCHAR(50) NOT NULL PRIMARY KEY,
    Name NVARCHAR(200) NOT NULL,
    Country NVARCHAR(100) NOT NULL,
    MaterialType NVARCHAR(50) NOT NULL,
    Rating FLOAT NOT NULL
)
GO

CREATE TABLE material (
    MaterialId NVARCHAR(50) NOT NULL PRIMARY KEY,
    SupplierId NVARCHAR(50) NOT NULL REFERENCES supplier(SupplierId),
    Type NVARCHAR(50) NOT NULL,
    LotNumber NVARCHAR(50) NOT NULL,
    ReceivedDate DATE NOT NULL,
    QuantityKg FLOAT NOT NULL
)
GO

CREATE TABLE inventory (
    InventoryId NVARCHAR(50) NOT NULL PRIMARY KEY,
    FactoryId NVARCHAR(50) NOT NULL,
    MaterialId NVARCHAR(50) NOT NULL REFERENCES material(MaterialId),
    QuantityOnHand FLOAT NOT NULL,
    ReorderLevel FLOAT NOT NULL,
    LastUpdated DATETIME2 NOT NULL
)
GO

CREATE TABLE batch_material_usage (
    UsageId NVARCHAR(50) NOT NULL PRIMARY KEY,
    -- Loose reference to the Eventhouse's silver_batch -- cross-engine,
    -- not enforced with a real FK, same reasoning as shipment.BatchId
    -- below.
    BatchId NVARCHAR(100) NOT NULL,
    MaterialId NVARCHAR(50) NOT NULL REFERENCES material(MaterialId),
    QuantityKg FLOAT NOT NULL
)
GO

CREATE TABLE shipment (
    ShipmentId NVARCHAR(50) NOT NULL PRIMARY KEY,
    FromFactoryId NVARCHAR(50) NOT NULL,
    ToLocationId NVARCHAR(50) NOT NULL,
    -- Loose reference to the Eventhouse's silver_batch -- cross-engine,
    -- not enforced with a real FK (Fabric SQL DB and Eventhouse are
    -- separate engines/plane by design, see the medallion-layers memo).
    BatchId NVARCHAR(100) NOT NULL,
    Carrier NVARCHAR(100) NOT NULL,
    DepartDate DATE NOT NULL,
    ArriveDate DATE NULL,
    Status NVARCHAR(20) NOT NULL
)
GO

CREATE TABLE customer (
    CustomerId NVARCHAR(50) NOT NULL PRIMARY KEY,
    Name NVARCHAR(200) NOT NULL,
    Country NVARCHAR(100) NOT NULL,
    Segment NVARCHAR(50) NOT NULL,
    CreditLimit FLOAT NOT NULL
)
GO

CREATE TABLE product (
    ProductId NVARCHAR(50) NOT NULL PRIMARY KEY,
    Name NVARCHAR(200) NOT NULL,
    -- Loose reference to the Eventhouse-side recipe dimension (also
    -- mirrored into the Ontology's Lakehouse) -- not a real FK, same
    -- cross-engine reasoning as shipment.BatchId above.
    RecipeId NVARCHAR(50) NOT NULL,
    PackagingType NVARCHAR(50) NOT NULL,
    SKU NVARCHAR(50) NOT NULL
)
GO

CREATE TABLE sales_order (
    OrderId NVARCHAR(50) NOT NULL PRIMARY KEY,
    CustomerId NVARCHAR(50) NOT NULL REFERENCES customer(CustomerId),
    OrderDate DATE NOT NULL,
    Status NVARCHAR(20) NOT NULL,
    TotalAmount FLOAT NOT NULL,
    Currency NVARCHAR(10) NOT NULL
)
GO

CREATE TABLE order_line (
    OrderLineId NVARCHAR(50) NOT NULL PRIMARY KEY,
    OrderId NVARCHAR(50) NOT NULL REFERENCES sales_order(OrderId),
    ProductId NVARCHAR(50) NOT NULL REFERENCES product(ProductId),
    QuantityKg FLOAT NOT NULL,
    UnitPrice FLOAT NOT NULL
)
GO

CREATE TABLE invoice (
    InvoiceId NVARCHAR(50) NOT NULL PRIMARY KEY,
    OrderId NVARCHAR(50) NOT NULL REFERENCES sales_order(OrderId),
    IssueDate DATE NOT NULL,
    DueDate DATE NOT NULL,
    AmountDue FLOAT NOT NULL,
    PaidDate DATE NULL
)
GO

-- ============================================================================
-- Fabric OBO POC — Step 1: Create Tables
-- ============================================================================
-- Run this in the Fabric Warehouse SQL editor.
-- This creates the two tables needed for the POC:
--   1. Accounts     — the business data table (protected by RLS)
--   2. RepUserMapping — maps Entra user emails to RepCodes (used by RLS)
-- ============================================================================

-- Accounts table: each row belongs to a RepCode
CREATE TABLE dbo.Accounts (
    AccountId   INT           NOT NULL PRIMARY KEY,
    AccountName NVARCHAR(100) NOT NULL,
    Balance     DECIMAL(18,2) NOT NULL,
    CreatedDate DATE          NOT NULL,
    Region      NVARCHAR(50)  NOT NULL,
    RepCode     NVARCHAR(20)  NOT NULL
);

-- Rep-to-user mapping table (used by the RLS predicate function)
-- This table tells Fabric which Entra user owns which RepCode.
CREATE TABLE dbo.RepUserMapping (
    RepCode   NVARCHAR(20)  NOT NULL,
    UserEmail NVARCHAR(256) NOT NULL,
    PRIMARY KEY (RepCode, UserEmail)
);

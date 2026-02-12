-- ============================================================================
-- Fabric OBO POC — Step 3: Create Row-Level Security (RLS) Policy
-- ============================================================================
-- Run this in the Fabric Warehouse SQL editor AFTER running 01 and 02 scripts.
--
-- This creates:
--   1. A predicate function that checks the RepUserMapping table
--   2. A security policy that applies the predicate to the Accounts table
--
-- How it works:
--   - When any user runs SELECT * FROM dbo.Accounts, Fabric automatically
--     calls fn_SecurityPredicate for each row
--   - The function checks: "Is the current user's email mapped to this row's
--     RepCode in RepUserMapping?"
--   - If yes → row is returned. If no → row is silently filtered out.
--   - Workspace admins (db_owner) bypass RLS and see all rows.
--
-- The user's email comes from USER_NAME(), which Fabric sets based on the
-- identity in the OBO token — this is why the OBO flow matters.
-- ============================================================================

-- Security predicate function
CREATE FUNCTION dbo.fn_SecurityPredicate(@RepCode NVARCHAR(20))
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS result
    WHERE
        -- Admin bypass: workspace admins see all data
        IS_MEMBER('db_owner') = 1
        OR
        -- Normal user: only see rows where their email maps to this RepCode
        @RepCode IN (
            SELECT rum.RepCode
            FROM dbo.RepUserMapping AS rum
            WHERE rum.UserEmail = USER_NAME()
        );

-- Apply the RLS policy to the Accounts table
CREATE SECURITY POLICY dbo.AccountsFilter
    ADD FILTER PREDICATE dbo.fn_SecurityPredicate(RepCode) ON dbo.Accounts
    WITH (STATE = ON);

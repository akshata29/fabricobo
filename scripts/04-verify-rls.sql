-- ============================================================================
-- Fabric OBO POC — Verify RLS Setup
-- ============================================================================
-- Run this in the Fabric Warehouse SQL editor to confirm everything is working.
-- Run as an admin user to see all data (admin bypass).
-- ============================================================================

-- 1. Check all accounts are present (admin should see all 7)
SELECT * FROM dbo.Accounts ORDER BY AccountId;

-- 2. Check user mappings
SELECT * FROM dbo.RepUserMapping;

-- 3. Check who the current user is (this is what RLS uses)
SELECT USER_NAME() AS CurrentUser;

-- 4. Check RLS policy exists and is enabled
SELECT
    sp.name            AS PolicyName,
    sp.is_enabled      AS IsEnabled,
    o.name             AS ProtectedTable,
    sp.create_date     AS CreatedDate
FROM sys.security_policies sp
JOIN sys.security_predicates pred ON sp.object_id = pred.object_id
JOIN sys.objects o ON pred.target_object_id = o.object_id;

-- 5. Summary by RepCode (admin sees both; users see only their own)
SELECT
    RepCode,
    COUNT(*)          AS AccountCount,
    SUM(Balance)      AS TotalBalance,
    STRING_AGG(AccountName, ', ') AS AccountNames
FROM dbo.Accounts
GROUP BY RepCode;

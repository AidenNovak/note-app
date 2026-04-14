"""
D1 → PostgreSQL Migration Script
=================================
Migrates users and OAuth accounts from Cloudflare D1 (Better Auth)
to Supabase PostgreSQL (unified FastAPI backend).

Run once during backend consolidation. Already executed 2026-04-14.

Notes:
- D1 IDs (random strings) → PG IDs (UUID v4)
- Better Auth scrypt passwords → marked as $migrated_from_d1$ (users must reset)
- Billing tables were empty in D1, nothing to migrate
- Session table not migrated (JWT replaces cookie sessions)
"""

import psycopg2
import uuid
from datetime import datetime, timezone

PG_DSN = "postgresql://postgres:PASSWORD@db.hsluvhwjovcgnllypemf.supabase.co:5432/postgres"

# Data exported from D1 via: npx wrangler d1 execute atelier-db --remote --json
D1_USERS = [
    {"id": "peKu9M3RXy32ORMrjwkqOtcg39WYhZmC", "name": "Test User2", "email": "test2@test.local", "email_verified": False, "created_at": 1775418784},
    {"id": "6S0mPI2LM2UDNL4aleaM6wFIFqNO4uCF", "name": "E2E Tester", "email": "e2e-1775418797112@test.local", "email_verified": False, "created_at": 1775418807},
    {"id": "bpiZsqTRBik2EE4PwD44NRpnZJDgfidd", "name": "E2E Tester", "email": "e2e-1775419127073@test.local", "email_verified": False, "created_at": 1775419137},
    {"id": "7TfsAgqFmhbOCi8VSoZBdFaqFd8Gj5Ay", "name": "Dev Tester", "email": "dev@test.local", "email_verified": True, "created_at": 1776106813},
]

D1_ACCOUNTS = [
    {"id": "f3jaFDBtxZ1jf67nwnV6d2QkYMayKoYy", "provider_id": "credential", "user_id": "peKu9M3RXy32ORMrjwkqOtcg39WYhZmC", "created_at": 1775418784},
    {"id": "01fYIX2mI7thfVKbVu5zJZ14b4tNXJgX", "provider_id": "credential", "user_id": "6S0mPI2LM2UDNL4aleaM6wFIFqNO4uCF", "created_at": 1775418807},
    {"id": "yAuQTJeirPZ9YUVMqjGM1x6mBJIKL6Ty", "provider_id": "credential", "user_id": "bpiZsqTRBik2EE4PwD44NRpnZJDgfidd", "created_at": 1775419137},
    {"id": "qSW38XxoyAmd2Cb392sxr3DINYqBKsxe", "provider_id": "credential", "user_id": "7TfsAgqFmhbOCi8VSoZBdFaqFd8Gj5Ay", "created_at": 1776106813},
]

def ts_to_dt(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc)

def main():
    id_map = {u["id"]: str(uuid.uuid4()) for u in D1_USERS}
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    try:
        for u in D1_USERS:
            new_id = id_map[u["id"]]
            username = u["email"].split("@")[0]
            cur.execute("""
                INSERT INTO users (id, username, email, hashed_password, display_name,
                                 email_verified, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, '$migrated_from_d1$', %s, %s, true, %s, %s)
                ON CONFLICT (email) DO NOTHING
            """, (new_id, username, u["email"], u["name"],
                  u["email_verified"], ts_to_dt(u["created_at"]), ts_to_dt(u["created_at"])))
            print(f"User: {u['email']} → {new_id[:8]}...")

        for a in D1_ACCOUNTS:
            new_user_id = id_map[a["user_id"]]
            cur.execute("""
                INSERT INTO oauth_accounts (id, user_id, provider, provider_account_id, created_at)
                VALUES (%s, %s, 'credential_migrated', %s, %s)
                ON CONFLICT DO NOTHING
            """, (str(uuid.uuid4()), new_user_id, a["id"], ts_to_dt(a["created_at"])))

        conn.commit()
        cur.execute("SELECT COUNT(*) FROM users")
        print(f"PG users: {cur.fetchone()[0]}")
    except Exception as e:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()

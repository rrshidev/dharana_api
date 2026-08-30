import sqlite3

c = sqlite3.connect("/app/test.db")
print("=== USERS ===")
for r in c.execute("SELECT id,name,email,telegram_id,is_admin,avatar_url FROM app_users ORDER BY id"):
    print(r)
print()
print("=== TABLES ===")
for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    print(r[0])

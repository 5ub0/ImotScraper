import sqlite3
conn = sqlite3.connect('data/imot_scraper.db')

print('Tables:', [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()])
print()
print('properties cols:', [r[1] for r in conn.execute('PRAGMA table_info(properties)').fetchall()])
print()
print('properties DDL:')
row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='properties'").fetchone()
print(row[0] if row else 'NOT FOUND')
print()
print('Objects referencing properties_old:')
for r in conn.execute("SELECT type, name, sql FROM sqlite_master WHERE sql LIKE '%properties_old%'").fetchall():
    print(r)
print()
print('All triggers:')
for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall():
    print(r)
print()
print('All indexes on properties:')
for r in conn.execute("PRAGMA index_list(properties)").fetchall():
    print(r)
print()
print('Full sqlite_master:')
for r in conn.execute("SELECT type, name, tbl_name FROM sqlite_master ORDER BY type, name").fetchall():
    print(r)
conn.close()

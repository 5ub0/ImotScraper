import sys
sys.path.insert(0, '.')
from database.db_manager import DatabaseManager

db = DatabaseManager('data/imot_scraper.db')
conn = db._get_connection()

print('=== Duplicate record_ids ===')
rows = conn.execute(
    'SELECT record_id, COUNT(*) as cnt FROM properties GROUP BY record_id HAVING cnt > 1'
).fetchall()
for r in rows:
    print(dict(r))
    detail = conn.execute(
        'SELECT id, record_id, search_name, title, first_seen FROM properties WHERE record_id = ?',
        (r['record_id'],)
    ).fetchall()
    for d in detail:
        print('  ', dict(d))

print()
print('=== searches table ===')
for r in conn.execute('SELECT id, search_name FROM searches').fetchall():
    print(dict(r))

print()
print('=== properties distinct search_name values ===')
for r in conn.execute('SELECT DISTINCT search_name FROM properties').fetchall():
    print(dict(r))

print()
print('=== properties table columns ===')
for r in conn.execute('PRAGMA table_info(properties)').fetchall():
    print(dict(r))

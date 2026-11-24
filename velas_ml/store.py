"""Base local: deduplicação, preferências e exclusões persistentes."""
import json
import sqlite3


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS purchases (
                key TEXT PRIMARY KEY, order_id TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY, included INTEGER NOT NULL,
                cost_center TEXT NOT NULL, notes TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ignored_orders (order_id TEXT PRIMARY KEY);
        ''')

    def close(self):
        self.db.close()

    def upsert(self, buyer, order_ids, rows):
        previous = self.db.execute("SELECT value FROM metadata WHERE key='buyer'").fetchone()
        if previous and previous[0] != buyer:
            raise ValueError('A base pertence a outra conta. Configure outra pasta de dados e outra saída.')
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('buyer', ?)", (buyer,))
            # Só substitui pedidos consultados com sucesso; mantém o histórico antigo.
            self.db.executemany('DELETE FROM purchases WHERE order_id=?', [(o,) for o in order_ids])
            self.db.executemany('INSERT INTO purchases VALUES (?, ?, ?)', [
                (r['key'], r['order_id'], json.dumps(r, ensure_ascii=False)) for r in rows
            ])

    def preferences(self, preferences):
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO preferences VALUES (?, ?, ?, ?)', preferences)

    def ignore(self, order_id, ignored=True):
        if not self.db.execute('SELECT 1 FROM purchases WHERE order_id=?', (order_id,)).fetchone():
            raise ValueError('Pedido não encontrado na base local.')
        with self.db:
            if ignored:
                self.db.execute('INSERT OR IGNORE INTO ignored_orders VALUES (?)', (order_id,))
            else:
                self.db.execute('DELETE FROM ignored_orders WHERE order_id=?', (order_id,))
                self.db.execute('UPDATE preferences SET included=1 WHERE key IN '
                                '(SELECT key FROM purchases WHERE order_id=?)', (order_id,))

    def rows(self):
        result = []
        for payload, included, center, notes, ignored in self.db.execute('''
            SELECT p.payload, COALESCE(f.included, 1), COALESCE(f.cost_center, ''),
                   COALESCE(f.notes, ''), i.order_id
            FROM purchases p LEFT JOIN preferences f ON p.key=f.key
            LEFT JOIN ignored_orders i ON p.order_id=i.order_id
            ORDER BY json_extract(p.payload, '$.date'), p.order_id, p.key
        '''):
            row = json.loads(payload)
            row.update(included=bool(included) and ignored is None, cost_center=center, notes=notes)
            result.append(row)
        return result

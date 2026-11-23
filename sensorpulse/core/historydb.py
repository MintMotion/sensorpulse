"""Historial largo en SQLite (~/.local/state/sensorpulse/history.sqlite).

- Medias por minuto (con mínimo y máximo) durante 7 días.
- Medias cada 10 minutos durante 30 días.
- Registro de alarmas (últimas 1000).

Se escribe una sola vez por minuto, en una transacción, para no gastar el disco.
"""

import json
import os
import sqlite3
import sys
import time

from .. import PACKAGE

MINUTE_KEEP = 7 * 86400
TEN_KEEP = 30 * 86400
ALARM_KEEP = 1000
MAX_POINTS = 720


def state_dir():
    # la especificación XDG manda ignorar las rutas relativas
    base = os.environ.get("XDG_STATE_HOME") or ""
    if not os.path.isabs(base):
        base = os.path.expanduser("~/.local/state")
    return os.path.join(base, PACKAGE)


def is_corruption(e):
    """¿Este error de SQLite significa que el fichero está dañado (y no p. ej. disco lleno)?"""
    msg = str(e).lower()
    return isinstance(e, sqlite3.DatabaseError) and (
        "malformed" in msg or "not a database" in msg or "corrupt" in msg)


def quarantine(path):
    """Aparta una base dañada (con sus -wal y -shm: un -wal viejo junto a una base nueva
    podría mezclarse con ella) como .corrupt-FECHA. Devuelve el nombre nuevo."""
    dest = "%s.corrupt-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
    for ext in ("", "-wal", "-shm"):
        try:
            os.replace(path + ext, dest + ext)
        except FileNotFoundError:
            pass
    return dest


def open_db(path=None):
    """Abre el historial; si el fichero está dañado, lo aparta y empieza uno nuevo (la 0.3.5
    se quedaba sin historial para siempre si la base estaba dañada al arrancar)."""
    path = path or db_path()
    try:
        return HistoryDB(path)
    except sqlite3.DatabaseError as e:
        if not is_corruption(e):
            raise
        dest = quarantine(path)
        print("sensorpulse: historial dañado (%s), apartado como %s" % (e, dest), file=sys.stderr)
        return HistoryDB(path)


def db_path():
    return os.path.join(state_dir(), "history.sqlite")


def bucket_step(seconds):
    """Segundos que agrupa cada punto de query(seconds); t es el INICIO del grupo."""
    return max(60 if seconds <= MINUTE_KEEP else 600, int(seconds / MAX_POINTS))


class _Acc:
    __slots__ = ("sum", "n", "mn", "mx")

    def __init__(self):
        self.sum, self.n, self.mn, self.mx = 0.0, 0, None, None

    def add(self, v):
        self.sum += v
        self.n += 1
        self.mn = v if self.mn is None else min(self.mn, v)
        self.mx = v if self.mx is None else max(self.mx, v)

    def row(self):
        return (self.sum / self.n, self.mn, self.mx, self.n)


_SCHEMA = """
    CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY, cid TEXT UNIQUE);
    CREATE TABLE IF NOT EXISTS m1 (t INTEGER, ch INTEGER, avg REAL, mn REAL, mx REAL, n INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS m10 (t INTEGER, ch INTEGER, avg REAL, mn REAL, mx REAL, n INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS alarms (t REAL, ev TEXT);
    CREATE INDEX IF NOT EXISTS alarms_t ON alarms (t);
"""


class HistoryDB:
    def __init__(self, path=None):
        self.path = path or db_path()
        d = os.path.dirname(self.path)
        os.makedirs(d, mode=0o700, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        # el historial dice cuándo se usa el equipo: solo para su dueño (también -wal y -shm,
        # que SQLite crea con los permisos del fichero principal)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)
        self._migrate()
        self.db.executescript("""
            CREATE UNIQUE INDEX IF NOT EXISTS m1_ch_t ON m1 (ch, t);
            CREATE UNIQUE INDEX IF NOT EXISTS m10_ch_t ON m10 (ch, t);
            CREATE INDEX IF NOT EXISTS m1_t ON m1 (t);
            CREATE INDEX IF NOT EXISTS m10_t ON m10 (t);
        """)
        self.db.commit()
        for ext in ("-wal", "-shm"):
            try:
                os.chmod(self.path + ext, 0o600)
            except OSError:
                pass
        self._ids = dict((cid, i) for i, cid in self.db.execute("SELECT id, cid FROM channels"))
        self._m1 = {}
        self._m10 = {}
        self._cur1 = None
        self._cur10 = None
        self._last_prune = 0

    def _migrate(self):
        """Bases de la 0.3.3 o anteriores: sin columna «n» (nº de muestras) y con filas
        repetidas del mismo minuto (una por cada reinicio del servicio). Se unifican."""
        for table in ("m1", "m10"):
            cols = [r[1] for r in self.db.execute("PRAGMA table_info(%s)" % table)]
            if "n" in cols:
                continue
            self.db.executescript("""
                DROP INDEX IF EXISTS %(t)s_ch_t;
                ALTER TABLE %(t)s RENAME TO %(t)s_old;
                CREATE TABLE %(t)s (t INTEGER, ch INTEGER, avg REAL, mn REAL, mx REAL, n INTEGER DEFAULT 1);
                INSERT INTO %(t)s SELECT t, ch, AVG(avg), MIN(mn), MAX(mx), COUNT(*)
                    FROM %(t)s_old GROUP BY t, ch;
                DROP TABLE %(t)s_old;
            """ % {"t": table})

    def _ch(self, cid):
        i = self._ids.get(cid)
        if i is None:
            cur = self.db.execute("INSERT OR IGNORE INTO channels (cid) VALUES (?)", (cid,))
            # si la fila ya existía, lastrowid puede ser el de una inserción anterior
            if cur.rowcount == 1 and cur.lastrowid:
                i = cur.lastrowid
            else:
                i = self.db.execute("SELECT id FROM channels WHERE cid=?", (cid,)).fetchone()[0]
            self._ids[cid] = i
        return i

    def add(self, now, values):
        """values: iterable de (channel_id, valor)."""
        m1 = int(now // 60) * 60
        m10 = int(now // 600) * 600
        wrote = False
        try:
            if self._cur1 is not None and m1 != self._cur1:
                self._insert("m1", self._cur1, self._m1)
                wrote = True
            if self._cur10 is not None and m10 != self._cur10:
                self._insert("m10", self._cur10, self._m10)
                wrote = True
            if wrote:
                self._commit(now)
        except sqlite3.Error:
            # nada a medias: sin rollback, el reintento volvería a sumar las filas ya
            # actualizadas en esta transacción (medias con n duplicado)
            try:
                self.db.rollback()
            except sqlite3.Error:
                pass
            raise
        if self._cur1 is not None and m1 != self._cur1:
            self._m1 = {}
        if self._cur10 is not None and m10 != self._cur10:
            self._m10 = {}
        self._cur1, self._cur10 = m1, m10
        for cid, v in values:
            if v is None:
                continue
            self._m1.setdefault(cid, _Acc()).add(v)
            self._m10.setdefault(cid, _Acc()).add(v)

    def _insert(self, table, t, accs):
        """Guarda el periodo. Si ya hay fila del mismo minuto/canal (el servicio se reinició
        a mitad de minuto) se combina ponderando por número de muestras, sin duplicar."""
        for cid, a in accs.items():
            if not a.n:
                continue
            ch = self._ch(cid)
            avg, mn, mx, n = a.row()
            cur = self.db.execute(
                "UPDATE %s SET avg=(avg*n+?*?)/(n+?), mn=MIN(mn,?), mx=MAX(mx,?), n=n+? "
                "WHERE t=? AND ch=?" % table, (avg, n, n, mn, mx, n, t, ch))
            if cur.rowcount == 0:
                self.db.execute("INSERT INTO %s (t,ch,avg,mn,mx,n) VALUES (?,?,?,?,?,?)" % table,
                                (t, ch, avg, mn, mx, n))

    def _commit(self, now):
        if now - self._last_prune > 3600:
            self._last_prune = now
            self.db.execute("DELETE FROM m1 WHERE t < ?", (now - MINUTE_KEEP,))
            self.db.execute("DELETE FROM m10 WHERE t < ?", (now - TEN_KEEP,))
            self.db.execute("DELETE FROM alarms WHERE rowid NOT IN "
                            "(SELECT rowid FROM alarms ORDER BY t DESC LIMIT ?)", (ALARM_KEEP,))
        self.db.commit()

    def query(self, cid, seconds):
        """Lista de [t, media, mínimo, máximo], como mucho MAX_POINTS puntos."""
        i = self._ids.get(cid)
        table = "m1" if seconds <= MINUTE_KEEP else "m10"
        since = time.time() - seconds
        step = bucket_step(seconds)
        rows = []
        if i is not None:
            rows = self.db.execute(
                "SELECT (t / ?) * ?, SUM(avg*n)/SUM(n), MIN(mn), MAX(mx), SUM(n) FROM %s "
                "WHERE ch=? AND t>=? GROUP BY t / ? ORDER BY 1" % table,
                (step, step, i, since, step)).fetchall()
        out = [[r[0], round(r[1], 2), r[2], r[3], r[4]] for r in rows]
        # el periodo en curso (aún en memoria, sin guardar) también cuenta
        cur_t, accs = (self._cur1, self._m1) if table == "m1" else (self._cur10, self._m10)
        a = accs.get(cid)
        if cur_t is not None and a is not None and a.n:
            avg, mn, mx, n = a.row()
            bucket = (cur_t // step) * step
            if out and out[-1][0] == bucket:
                p = out[-1]    # media ponderada por nº de muestras de cada parte
                out[-1] = [bucket, round((p[1] * p[4] + avg * n) / (p[4] + n), 2),
                           min(p[2], mn), max(p[3], mx), p[4] + n]
            else:
                out.append([bucket, round(avg, 2), mn, mx, n])
        return [r[:4] for r in out]

    def add_alarm(self, ev):
        self.db.execute("INSERT INTO alarms VALUES (?, ?)", (ev.get("time", time.time()),
                                                            json.dumps(ev)))
        self.db.commit()

    def alarms(self, limit=100):
        rows = self.db.execute("SELECT ev FROM alarms ORDER BY t DESC LIMIT ?", (limit,))
        out = []
        for (ev,) in rows:
            try:
                out.append(json.loads(ev))
            except ValueError:
                pass
        out.reverse()
        return out

    def close(self):
        try:
            if self._cur1 is not None:
                self._insert("m1", self._cur1, self._m1)
                self._insert("m10", self._cur10, self._m10)
                self.db.commit()
            self.db.execute("PRAGMA optimize")
            self.db.close()
        except sqlite3.Error:
            pass

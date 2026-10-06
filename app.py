import os, sqlite3, calendar, uuid
from datetime import date, datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-change-me')
DB = os.getenv('DATABASE_PATH', os.path.join(os.path.dirname(__file__), 'timesheet.db'))
UPLOAD = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
os.makedirs(UPLOAD, exist_ok=True)

@app.template_filter('g')
def fmt_g(value):
    try:
        return f'{float(value):g}'
    except (TypeError, ValueError):
        return value

LEAVE_TYPES = ['Worked','Sick','Vacation','Personal','Unpaid','Other']

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn

def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT UNIQUE NOT NULL,
          display_name TEXT NOT NULL,
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'employee',
          active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS schedules (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          effective_date TEXT NOT NULL,
          mon REAL DEFAULT 8, tue REAL DEFAULT 8, wed REAL DEFAULT 8, thu REAL DEFAULT 8, fri REAL DEFAULT 8, sat REAL DEFAULT 0, sun REAL DEFAULT 0,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS balances (
          user_id INTEGER PRIMARY KEY,
          sick REAL DEFAULT 0, vacation REAL DEFAULT 0, personal REAL DEFAULT 0,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          work_date TEXT NOT NULL,
          notes TEXT DEFAULT '',
          updated_by INTEGER,
          updated_at TEXT NOT NULL,
          UNIQUE(user_id, work_date),
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS entry_segments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entry_id INTEGER NOT NULL,
          segment_type TEXT NOT NULL,
          hours REAL NOT NULL,
          FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER,
          actor_id INTEGER,
          work_date TEXT,
          message TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        ''')
        defaults = {'site_name':'Timesheet','banner_text':'Time Management','logo_path':'','banner_path':''}
        for k,v in defaults.items():
            c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',(k,v))
        admin_user = os.getenv('ADMIN_USERNAME','admin')
        admin_pass = os.getenv('ADMIN_PASSWORD','changeme')
        row = c.execute('SELECT id FROM users WHERE username=?',(admin_user,)).fetchone()
        if not row:
            c.execute('INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)',
                      (admin_user,'Administrator',generate_password_hash(admin_pass),'admin'))
            uid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO balances(user_id) VALUES(?)',(uid,))
            c.execute('INSERT INTO schedules(user_id,effective_date) VALUES(?,?)',(uid,date.today().isoformat()))

init_db()

def settings():
    with db() as c:
        return {r['key']:r['value'] for r in c.execute('SELECT key,value FROM settings')}

@app.context_processor
def inject():
    return {'site': settings(), 'today': date.today(), 'leave_types': LEAVE_TYPES}

def login_required(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if 'uid' not in session: return redirect(url_for('login'))
        return fn(*a, **kw)
    return wrap

def admin_required(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if session.get('role') != 'admin':
            flash('Administrator access required.','error'); return redirect(url_for('calendar_view'))
        return fn(*a, **kw)
    return wrap

def schedule_for(uid, d):
    with db() as c:
        row = c.execute('SELECT * FROM schedules WHERE user_id=? AND effective_date<=? ORDER BY effective_date DESC LIMIT 1',(uid,d.isoformat())).fetchone()
    if not row: return 0
    return float(row[['mon','tue','wed','thu','fri','sat','sun'][d.weekday()]])

def remaining_balances(uid):
    with db() as c:
        base = c.execute('SELECT sick,vacation,personal FROM balances WHERE user_id=?',(uid,)).fetchone()
        used_rows = c.execute("""SELECT s.segment_type, COALESCE(SUM(s.hours),0) used FROM entry_segments s JOIN entries e ON e.id=s.entry_id WHERE e.user_id=? AND s.segment_type IN ('Sick','Vacation','Personal') GROUP BY s.segment_type""",(uid,)).fetchall()
    used = {r['segment_type'].lower():float(r['used']) for r in used_rows}
    if not base: return {'sick':0,'vacation':0,'personal':0}
    return {k: float(base[k]) - used.get(k,0) for k in ['sick','vacation','personal']}

def get_entry(uid, dstr):
    with db() as c:
        e = c.execute('SELECT * FROM entries WHERE user_id=? AND work_date=?',(uid,dstr)).fetchone()
        if not e: return None, []
        segs = c.execute('SELECT * FROM entry_segments WHERE entry_id=? ORDER BY id',(e['id'],)).fetchall()
        return e, segs

def save_entry(uid, dstr, segs, notes, actor):
    now = datetime.now().isoformat(timespec='seconds')
    with db() as c:
        old = c.execute('SELECT id FROM entries WHERE user_id=? AND work_date=?',(uid,dstr)).fetchone()
        if old:
            eid = old['id']; c.execute('DELETE FROM entry_segments WHERE entry_id=?',(eid,))
            c.execute('UPDATE entries SET notes=?,updated_by=?,updated_at=? WHERE id=?',(notes,actor,now,eid))
        else:
            c.execute('INSERT INTO entries(user_id,work_date,notes,updated_by,updated_at) VALUES(?,?,?,?,?)',(uid,dstr,notes,actor,now))
            eid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        for typ,hours in segs:
            if hours > 0: c.execute('INSERT INTO entry_segments(entry_id,segment_type,hours) VALUES(?,?,?)',(eid,typ,hours))
        summary = ', '.join(f'{h:g} {t}' for t,h in segs if h>0) or 'cleared'
        c.execute('INSERT INTO audit_log(user_id,actor_id,work_date,message,created_at) VALUES(?,?,?,?,?)',(uid,actor,dstr,f'Entry set to {summary}',now))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        with db() as c:
            u=c.execute('SELECT * FROM users WHERE username=? AND active=1',(request.form['username'],)).fetchone()
        if u and check_password_hash(u['password_hash'],request.form['password']):
            session.update(uid=u['id'],name=u['display_name'],role=u['role'])
            return redirect(url_for('calendar_view'))
        flash('Invalid username or password.','error')
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/')
@login_required
def calendar_view():
    uid = session['uid']
    if session.get('role')=='admin' and request.args.get('user'): uid=int(request.args['user'])
    y=int(request.args.get('year',date.today().year)); m=int(request.args.get('month',date.today().month))
    first=date(y,m,1); _,days=calendar.monthrange(y,m)
    rows=[]
    for i in range(1,days+1):
        d=date(y,m,i); sched=schedule_for(uid,d); e,segs=get_entry(uid,d.isoformat())
        totals={t:0 for t in LEAVE_TYPES}
        for s in segs: totals[s['segment_type']] = totals.get(s['segment_type'],0)+float(s['hours'])
        total=sum(totals.values())
        rows.append({'date':d,'scheduled':sched,'entry':e,'segments':segs,'totals':totals,'total':total,'delta':total-sched})
    with db() as c:
        user=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); bal=remaining_balances(uid)
        users=c.execute('SELECT id,display_name FROM users WHERE active=1 ORDER BY display_name').fetchall() if session.get('role')=='admin' else []
    prev=(first-timedelta(days=1)); nxt=(date(y,m,days)+timedelta(days=1))
    return render_template('calendar.html',rows=rows,user=user,bal=bal,users=users,year=y,month=m,month_name=first.strftime('%B %Y'),prev=prev,nxt=nxt)

@app.post('/quick-worked/<int:uid>/<dstr>')
@login_required
def quick_worked(uid,dstr):
    if uid!=session['uid'] and session.get('role')!='admin': return ('Forbidden',403)
    d=datetime.strptime(dstr,'%Y-%m-%d').date()
    hrs=schedule_for(uid,d)
    existing,_ = get_entry(uid,dstr)
    if existing:
        flash('That day already has a timesheet entry. Use Edit to change it.','error')
        return redirect(request.referrer or url_for('calendar_view'))
    if hrs <= 0:
        flash('That day is not scheduled. Use Edit if you need to add unscheduled hours.','error')
        return redirect(request.referrer or url_for('calendar_view'))
    save_entry(uid,dstr,[('Worked',hrs)],'',session['uid'])
    flash(f'{hrs:g} worked hours recorded for {d.strftime("%A, %B %-d")}.','ok')
    return redirect(request.referrer or url_for('calendar_view'))

@app.route('/entry/<int:uid>/<dstr>', methods=['GET','POST'])
@login_required
def edit_entry(uid,dstr):
    if uid!=session['uid'] and session.get('role')!='admin': return ('Forbidden',403)
    d=datetime.strptime(dstr,'%Y-%m-%d').date()
    if request.method=='POST':
        segs=[]
        for typ,h in zip(request.form.getlist('type'),request.form.getlist('hours')):
            try: hv=float(h or 0)
            except: hv=0
            if typ in LEAVE_TYPES and hv>0: segs.append((typ,hv))
        save_entry(uid,dstr,segs,request.form.get('notes',''),session['uid'])
        return redirect(url_for('calendar_view',user=uid,year=d.year,month=d.month))
    e,segs=get_entry(uid,dstr)
    return render_template('entry.html',uid=uid,d=d,scheduled=schedule_for(uid,d),entry=e,segs=segs)

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    with db() as c: users=c.execute('SELECT u.*,b.sick,b.vacation,b.personal FROM users u LEFT JOIN balances b ON b.user_id=u.id ORDER BY display_name').fetchall()
    return render_template('users.html',users=users)

@app.route('/admin/user/new', methods=['GET','POST'])
@login_required
@admin_required
def new_user():
    if request.method=='POST':
        with db() as c:
            try:
                c.execute('INSERT INTO users(username,display_name,password_hash,role) VALUES(?,?,?,?)',(request.form['username'],request.form['display_name'],generate_password_hash(request.form['password']),request.form.get('role','employee')))
                uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                c.execute('INSERT INTO balances(user_id,sick,vacation,personal) VALUES(?,?,?,?)',(uid,float(request.form.get('sick') or 0),float(request.form.get('vacation') or 0),float(request.form.get('personal') or 0)))
                c.execute('INSERT INTO schedules(user_id,effective_date,mon,tue,wed,thu,fri,sat,sun) VALUES(?,?,?,?,?,?,?,?,?)',(uid,request.form['effective_date'],*[float(request.form.get(x) or 0) for x in ['mon','tue','wed','thu','fri','sat','sun']]))
                flash('Employee created.','ok'); return redirect(url_for('admin_users'))
            except sqlite3.IntegrityError: flash('That username already exists.','error')
    return render_template('user_form.html',u=None)

@app.route('/admin/user/<int:uid>', methods=['GET','POST'])
@login_required
@admin_required
def edit_user(uid):
    with db() as c:
        u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); bal=c.execute('SELECT * FROM balances WHERE user_id=?',(uid,)).fetchone(); sch=c.execute('SELECT * FROM schedules WHERE user_id=? ORDER BY effective_date DESC LIMIT 1',(uid,)).fetchone()
        if request.method=='POST':
            c.execute('UPDATE users SET display_name=?,role=?,active=? WHERE id=?',(request.form['display_name'],request.form['role'],1 if request.form.get('active') else 0,uid))
            if request.form.get('password'): c.execute('UPDATE users SET password_hash=? WHERE id=?',(generate_password_hash(request.form['password']),uid))
            c.execute('UPDATE balances SET sick=?,vacation=?,personal=? WHERE user_id=?',(float(request.form.get('sick') or 0),float(request.form.get('vacation') or 0),float(request.form.get('personal') or 0),uid))
            c.execute('INSERT INTO schedules(user_id,effective_date,mon,tue,wed,thu,fri,sat,sun) VALUES(?,?,?,?,?,?,?,?,?)',(uid,request.form['effective_date'],*[float(request.form.get(x) or 0) for x in ['mon','tue','wed','thu','fri','sat','sun']]))
            flash('Employee updated.','ok'); return redirect(url_for('admin_users'))
    return render_template('user_form.html',u=u,bal=bal,sch=sch)

@app.route('/admin/branding', methods=['GET','POST'])
@login_required
@admin_required
def branding():
    if request.method=='POST':
        values={'site_name':request.form.get('site_name','Timesheet').strip() or 'Timesheet','banner_text':request.form.get('banner_text','').strip()}
        for field,key in [('logo','logo_path'),('banner','banner_path')]:
            f=request.files.get(field)
            if f and f.filename:
                ext=os.path.splitext(secure_filename(f.filename))[1].lower()
                if ext in ['.png','.jpg','.jpeg','.webp','.gif','.svg']:
                    name=f'{field}_{uuid.uuid4().hex[:10]}{ext}'; f.save(os.path.join(UPLOAD,name)); values[key]='/static/uploads/'+name
        with db() as c:
            for k,v in values.items(): c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,v))
        flash('Branding updated.','ok'); return redirect(url_for('branding'))
    return render_template('branding.html')

@app.route('/my-report')
@login_required
def my_report():
    uid = session['uid']
    report_today = date.today()
    default_start = report_today - timedelta(days=report_today.weekday())
    start_s = request.args.get('start', default_start.isoformat())
    end_s = request.args.get('end', report_today.isoformat())
    try:
        start_d = datetime.strptime(start_s, '%Y-%m-%d').date()
        end_d = datetime.strptime(end_s, '%Y-%m-%d').date()
    except ValueError:
        flash('Please choose valid report dates.','error')
        start_d, end_d = default_start, report_today
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    with db() as c:
        user = c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        entries = c.execute("""SELECT e.id,e.work_date,e.notes,e.updated_at
            FROM entries e WHERE e.user_id=? AND e.work_date BETWEEN ? AND ?
            ORDER BY e.work_date""",(uid,start_d.isoformat(),end_d.isoformat())).fetchall()
        seg_rows = c.execute("""SELECT e.work_date,s.segment_type,s.hours
            FROM entry_segments s JOIN entries e ON e.id=s.entry_id
            WHERE e.user_id=? AND e.work_date BETWEEN ? AND ?
            ORDER BY e.work_date,s.id""",(uid,start_d.isoformat(),end_d.isoformat())).fetchall()
    by_date = {}
    totals = {t:0.0 for t in LEAVE_TYPES}
    for r in seg_rows:
        by_date.setdefault(r['work_date'], []).append(r)
        totals[r['segment_type']] = totals.get(r['segment_type'],0.0) + float(r['hours'])
    rows=[]
    for e in entries:
        d=datetime.strptime(e['work_date'],'%Y-%m-%d').date()
        segs=by_date.get(e['work_date'],[])
        rows.append({'date':d,'segments':segs,'total':sum(float(x['hours']) for x in segs),'notes':e['notes']})
    grand_total=sum(totals.values())
    return render_template('my_report.html',user=user,rows=rows,totals=totals,grand_total=grand_total,start=start_d,end=end_d)

@app.route('/admin/report')
@login_required
@admin_required
def report():
    y=int(request.args.get('year',date.today().year)); m=int(request.args.get('month',date.today().month))
    start=f'{y:04d}-{m:02d}-01'; end=f'{y:04d}-{m:02d}-{calendar.monthrange(y,m)[1]:02d}'
    with db() as c:
        rows=c.execute('''SELECT u.display_name, s.segment_type, ROUND(SUM(s.hours),2) hours
        FROM entry_segments s JOIN entries e ON s.entry_id=e.id JOIN users u ON e.user_id=u.id
        WHERE e.work_date BETWEEN ? AND ? GROUP BY u.id,s.segment_type ORDER BY u.display_name''',(start,end)).fetchall()
    grouped={}
    for r in rows: grouped.setdefault(r['display_name'],{})[r['segment_type']]=r['hours']
    return render_template('report.html',grouped=grouped,year=y,month=m,month_name=date(y,m,1).strftime('%B %Y'))

if __name__=='__main__': app.run(host='0.0.0.0',port=8080,debug=True)

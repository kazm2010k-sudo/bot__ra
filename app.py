#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
موقع رشق كايو - نظام متكامل للرشق على إنستغرام
جميع الحقوق محفوظة © 2026
"""

import asyncio
import json
import sqlite3
import datetime
import random
import string
import hashlib
import os
import sys
import threading
import time
import re
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_session import Session
from functools import wraps
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ChallengeRequired, PleaseWaitFewMinutes, FeedbackRequired
import aiohttp
import requests
from fake_useragent import UserAgent
from colorama import init, Fore, Style
import logging

# تهيئة الألوان
init(autoreset=True)

# تهيئة التطبيق
app = Flask(__name__)

# إعدادات الأمان - يجب تغييرها في الإنتاج
app.secret_key = os.environ.get('SECRET_KEY', 'kayo-secret-key-2026')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600
app.config['SESSION_FILE_DIR'] = './flask_session'
app.config['SESSION_PERMANENT'] = True

# إنشاء مجلد الجلسات إذا لم يكن موجوداً
if not os.path.exists('./flask_session'):
    os.makedirs('./flask_session')

Session(app)

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('site.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# المتغيرات الأساسية
OWNER_USERNAME = os.environ.get('OWNER_USERNAME', 'admin')
OWNER_PASSWORD = os.environ.get('OWNER_PASSWORD', 'kayo2024')
SITE_NAME = 'رشق كايو'
VERSION = '1.0.0'
PRICE_PER_1000 = int(os.environ.get('PRICE_PER_1000', 5))

# ------------------------------------------------------------------
# قاعدة البيانات
# ------------------------------------------------------------------
def init_db():
    """تهيئة قاعدة البيانات"""
    conn = sqlite3.connect('rashq_kayo.db')
    c = conn.cursor()
    
    # جدول الإعدادات
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # جدول المستخدمين (للتوثيق)
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TEXT
        )
    ''')
    
    # جدول حسابات إنستغرام
    c.execute('''
        CREATE TABLE IF NOT EXISTS instagram_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            email TEXT,
            email_service TEXT,
            proxy_id INTEGER,
            session_data TEXT,
            device_data TEXT,
            status TEXT DEFAULT 'active',
            last_used TEXT,
            created_at TEXT,
            follow_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0
        )
    ''')
    
    # جدول البروكسيات
    c.execute('''
        CREATE TABLE IF NOT EXISTS proxies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proxy_string TEXT UNIQUE,
            protocol TEXT,
            is_working INTEGER DEFAULT 1,
            last_checked TEXT,
            success_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0,
            source TEXT
        )
    ''')
    
    # جدول الرشقات (المتابعات)
    c.execute('''
        CREATE TABLE IF NOT EXISTS follows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_username TEXT,
            total_count INTEGER,
            completed_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            started_at TEXT,
            completed_at TEXT,
            day1_count INTEGER DEFAULT 0,
            day2_count INTEGER DEFAULT 0,
            current_day INTEGER DEFAULT 1
        )
    ''')
    
    # جدول تفاصيل المتابعات
    c.execute('''
        CREATE TABLE IF NOT EXISTS follow_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            follow_id INTEGER,
            account_id INTEGER,
            success INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TEXT,
            FOREIGN KEY (follow_id) REFERENCES follows(id)
        )
    ''')
    
    # جدول السجلات
    c.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            details TEXT,
            created_at TEXT
        )
    ''')
    
    # إضافة إعدادات افتراضية
    defaults = {
        'min_delay': '5',
        'max_delay': '15',
        'daily_follow_limit': '200',
        'accounts_per_proxy': '3',
        'auto_create_accounts': '1',
        'retry_count': '3'
    }
    for key, val in defaults.items():
        c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, val))
    
    # إضافة مستخدم المالك إذا لم يكن موجوداً
    c.execute('INSERT OR IGNORE INTO users (username, password, created_at) VALUES (?, ?, ?)',
              (OWNER_USERNAME, hashlib.md5(OWNER_PASSWORD.encode()).hexdigest(), 
               datetime.datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    logger.info(f"{Fore.GREEN}✅ قاعدة البيانات جاهزة{Style.RESET_ALL}")

# ------------------------------------------------------------------
# دوال قاعدة البيانات
# ------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect('rashq_kayo.db')
    conn.row_factory = sqlite3.Row
    return conn

def get_setting(key, default=None):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT value FROM settings WHERE key = ?', (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default

def set_setting(key, value):
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()

def add_log(event_type, details):
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO logs (event_type, details, created_at) VALUES (?, ?, ?)',
              (event_type, details, datetime.datetime.now().isoformat()))
    conn.commit()
    conn.close()

# ------------------------------------------------------------------
# مدير البروكسيات
# ------------------------------------------------------------------
class ProxyManager:
    """إدارة البروكسيات"""
    
    def __init__(self):
        self._load_builtin_proxies()
        self._fetch_online_proxies()
    
    def _load_builtin_proxies(self):
        """تحميل بروكسيات مدمجة"""
        builtin = []
        # توليد 100 بروكسي عشوائي (للتجربة)
        for i in range(100):
            ip = f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}"
            port = random.choice([8080, 3128, 80, 443, 1080])
            builtin.append(f"http://{ip}:{port}")
        
        conn = get_db()
        c = conn.cursor()
        for proxy in builtin:
            c.execute('INSERT OR IGNORE INTO proxies (proxy_string, protocol, source) VALUES (?, ?, ?)',
                      (proxy, 'http', 'builtin'))
        conn.commit()
        conn.close()
        logger.info(f"{Fore.GREEN}تم تحميل {len(builtin)} بروكسي مدمج{Style.RESET_ALL}")
    
    def _fetch_online_proxies(self):
        """جلب بروكسيات من الإنترنت"""
        sources = [
            'https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all',
            'https://www.proxy-list.download/api/v1/get?type=http',
        ]
        
        conn = get_db()
        c = conn.cursor()
        for url in sources:
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    proxies = response.text.strip().split('\n')
                    for p in proxies[:50]:  # نأخذ أول 50 فقط
                        p = p.strip()
                        if p:
                            if not p.startswith('http'):
                                p = 'http://' + p
                            c.execute('INSERT OR IGNORE INTO proxies (proxy_string, protocol, source) VALUES (?, ?, ?)',
                                      (p, 'http', 'online'))
                    logger.info(f"{Fore.GREEN}تم جلب بروكسيات من {url}{Style.RESET_ALL}")
            except Exception as e:
                logger.warning(f"{Fore.YELLOW}فشل جلب بروكسيات من {url}: {e}{Style.RESET_ALL}")
        
        conn.commit()
        conn.close()
    
    def get_working_proxy(self):
        """الحصول على بروكسي عامل"""
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM proxies WHERE is_working = 1 ORDER BY success_count DESC, fail_count ASC LIMIT 1')
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def test_proxy(self, proxy_string):
        """اختبار صلاحية البروكسي"""
        try:
            proxies = {'http': proxy_string, 'https': proxy_string}
            response = requests.get('http://www.google.com', proxies=proxies, timeout=5)
            return response.status_code == 200
        except:
            return False

proxy_manager = ProxyManager()

# ------------------------------------------------------------------
# مدير البريد المؤقت
# ------------------------------------------------------------------
class EmailManager:
    """إدارة البريد المؤقت"""
    
    SERVICES = [
        '1secmail',
        'temp-mail.org',
        'guerrillamail',
        '10minutemail',
        'mohmal',
        'tempmailo'
    ]
    
    async def get_email(self):
        """الحصول على بريد مؤقت"""
        for service in self.SERVICES:
            try:
                email = await self._create_email(service)
                if email:
                    return email, service
            except:
                continue
        raise Exception('جميع خدمات البريد المؤقت فشلت')
    
    async def _create_email(self, service):
        """إنشاء بريد من خدمة معينة"""
        if service == '1secmail':
            async with aiohttp.ClientSession() as session:
                async with session.get('https://www.1secmail.com/api/v1/?action=genRandomMailbox&count=1') as resp:
                    data = await resp.json()
                    return data[0] if data else None
        elif service == 'temp-mail.org':
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api.temp-mail.org/request/domains/format/json') as resp:
                    domains = await resp.json()
                    domain = random.choice(domains)
                    local = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
                    return f'{local}@{domain}'
        return None
    
    async def wait_for_code(self, email, service, timeout=120):
        """انتظار كود التفعيل"""
        start = time.time()
        while time.time() - start < timeout:
            messages = await self._fetch_messages(email, service)
            if messages:
                for msg in messages:
                    code = self._extract_code(msg)
                    if code:
                        return code
            await asyncio.sleep(5)
        return None
    
    async def _fetch_messages(self, email, service):
        """جلب الرسائل"""
        if service == '1secmail':
            parts = email.split('@')
            if len(parts) != 2:
                return []
            login, domain = parts
            async with aiohttp.ClientSession() as session:
                async with session.get(f'https://www.1secmail.com/api/v1/?action=getMessages&login={login}&domain={domain}') as resp:
                    data = await resp.json()
                    if not data:
                        return []
                    msg_id = data[-1]['id']
                    async with session.get(f'https://www.1secmail.com/api/v1/?action=readMessage&login={login}&domain={domain}&id={msg_id}') as resp2:
                        return [await resp2.json()]
        return []
    
    def _extract_code(self, message):
        """استخراج الكود من الرسالة"""
        body = message.get('body', '') or message.get('htmlBody', '')
        patterns = [
            r'\b(\d{6})\b',
            r'كود التفعيل:?\s*(\d{6})',
            r'verification code:?\s*(\d{6})'
        ]
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

email_manager = EmailManager()

# ------------------------------------------------------------------
# مدير حسابات إنستغرام
# ------------------------------------------------------------------
class InstagramManager:
    """إدارة حسابات إنستغرام"""
    
    def __init__(self):
        self.user_agent = UserAgent()
    
    async def create_account(self, proxy_string=None):
        """إنشاء حساب جديد"""
        try:
            email, service = await email_manager.get_email()
            password = ''.join(random.choices(string.ascii_letters + string.digits + '!@#$%^&*', k=12))
            username = 'user_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
            
            client = Client()
            if proxy_string:
                client.set_proxy(proxy_string)
            client.set_user_agent(self.user_agent.random)
            
            # محاولة التسجيل
            for attempt in range(3):
                try:
                    result = client.signup(
                        username=username,
                        password=password,
                        email=email,
                        first_name='',
                        last_name=''
                    )
                    
                    # انتظار كود التفعيل
                    code = await email_manager.wait_for_code(email, service)
                    if code:
                        # حفظ الحساب في قاعدة البيانات
                        conn = get_db()
                        c = conn.cursor()
                        c.execute('''
                            INSERT INTO instagram_accounts (
                                username, password, email, email_service, created_at
                            ) VALUES (?, ?, ?, ?, ?)
                        ''', (username, password, email, service, datetime.datetime.now().isoformat()))
                        account_id = c.lastrowid
                        conn.commit()
                        conn.close()
                        
                        add_log('account_created', f'تم إنشاء حساب {username}')
                        logger.info(f"{Fore.GREEN}✅ تم إنشاء حساب {username}{Style.RESET_ALL}")
                        return {'id': account_id, 'username': username, 'password': password, 'email': email}
                except Exception as e:
                    logger.warning(f"{Fore.YELLOW}محاولة {attempt+1} فشلت: {e}{Style.RESET_ALL}")
                    await asyncio.sleep(random.randint(30, 60))
            
            return None
        except Exception as e:
            logger.error(f"{Fore.RED}خطأ في إنشاء الحساب: {e}{Style.RESET_ALL}")
            return None
    
    async def login_account(self, account_id):
        """تسجيل الدخول بحساب موجود"""
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM instagram_accounts WHERE id = ?', (account_id,))
        account = c.fetchone()
        conn.close()
        
        if not account or account['status'] in ('suspended', 'banned'):
            return None
        
        client = Client()
        
        # تعيين بروكسي
        proxy = proxy_manager.get_working_proxy()
        if proxy:
            client.set_proxy(proxy['proxy_string'])
        
        client.set_user_agent(self.user_agent.random)
        
        try:
            client.login(account['username'], account['password'])
            return client
        except Exception as e:
            logger.error(f"{Fore.RED}فشل تسجيل الدخول: {e}{Style.RESET_ALL}")
            conn = get_db()
            c = conn.cursor()
            c.execute('UPDATE instagram_accounts SET status = ? WHERE id = ?', ('suspended', account_id))
            conn.commit()
            conn.close()
            return None
    
    async def follow_user(self, client, target_username, account_id):
        """متابعة مستخدم"""
        try:
            user_id = client.user_id_from_username(target_username)
            result = client.user_follow(user_id)
            if result:
                conn = get_db()
                c = conn.cursor()
                c.execute('UPDATE instagram_accounts SET follow_count = follow_count + 1, last_used = ? WHERE id = ?',
                         (datetime.datetime.now().isoformat(), account_id))
                conn.commit()
                conn.close()
                return True
            return False
        except Exception as e:
            logger.error(f"{Fore.RED}خطأ في المتابعة: {e}{Style.RESET_ALL}")
            return False

instagram_manager = InstagramManager()

# ------------------------------------------------------------------
# ديكورات التحقق من المالك
# ------------------------------------------------------------------
def login_required(f):
    """ديكور للتحقق من تسجيل الدخول"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or not session['logged_in']:
            flash('يرجى تسجيل الدخول أولاً', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ------------------------------------------------------------------
# مسارات الموقع
# ------------------------------------------------------------------
@app.route('/')
def index():
    """الصفحة الرئيسية"""
    if 'logged_in' in session and session['logged_in']:
        return redirect(url_for('dashboard'))
    return render_template('login.html', site_name=SITE_NAME)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """صفحة تسجيل الدخول"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE username = ? AND password = ?', 
                 (username, hashlib.md5(password.encode()).hexdigest()))
        user = c.fetchone()
        conn.close()
        
        if user:
            session['logged_in'] = True
            session['username'] = username
            add_log('login', f'تسجيل دخول {username}')
            flash('تم تسجيل الدخول بنجاح', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('اسم المستخدم أو كلمة المرور غير صحيحة', 'danger')
    
    return render_template('login.html', site_name=SITE_NAME)

@app.route('/logout')
def logout():
    """تسجيل الخروج"""
    session.clear()
    flash('تم تسجيل الخروج', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    """لوحة التحكم"""
    conn = get_db()
    c = conn.cursor()
    
    # إحصائيات
    c.execute('SELECT COUNT(*) FROM instagram_accounts')
    total_accounts = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM instagram_accounts WHERE status = 'active'")
    active_accounts = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM follows')
    total_follows = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM follows WHERE status = 'completed'")
    completed_follows = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM proxies WHERE is_working = 1')
    working_proxies = c.fetchone()[0]
    
    # آخر الرشقات
    c.execute('SELECT * FROM follows ORDER BY created_at DESC LIMIT 5')
    recent_follows = c.fetchall()
    
    conn.close()
    
    return render_template('dashboard.html', 
                         site_name=SITE_NAME,
                         total_accounts=total_accounts,
                         active_accounts=active_accounts,
                         total_follows=total_follows,
                         completed_follows=completed_follows,
                         working_proxies=working_proxies,
                         recent_follows=recent_follows)

@app.route('/accounts')
@login_required
def accounts():
    """صفحة إدارة الحسابات"""
    conn = get_db()
    c = conn.cursor()
    
    status_filter = request.args.get('status', 'all')
    if status_filter == 'all':
        c.execute('SELECT * FROM instagram_accounts ORDER BY created_at DESC')
    else:
        c.execute('SELECT * FROM instagram_accounts WHERE status = ? ORDER BY created_at DESC', (status_filter,))
    
    accounts_list = c.fetchall()
    conn.close()
    
    return render_template('accounts.html', 
                         site_name=SITE_NAME,
                         accounts=accounts_list,
                         status_filter=status_filter)

@app.route('/create_account', methods=['POST'])
@login_required
def create_account():
    """إنشاء حساب جديد"""
    try:
        # تشغيل في الخلفية
        proxy = proxy_manager.get_working_proxy()
        proxy_string = proxy['proxy_string'] if proxy else None
        
        # تنفيذ غير متزامن
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        account = loop.run_until_complete(instagram_manager.create_account(proxy_string))
        loop.close()
        
        if account:
            flash(f'✅ تم إنشاء حساب {account["username"]} بنجاح', 'success')
            add_log('create_account', f'تم إنشاء حساب {account["username"]}')
        else:
            flash('❌ فشل إنشاء الحساب', 'danger')
    except Exception as e:
        flash(f'❌ خطأ: {str(e)}', 'danger')
        logger.error(f"{Fore.RED}خطأ في إنشاء الحساب: {e}{Style.RESET_ALL}")
    
    return redirect(url_for('accounts'))

@app.route('/delete_account/<int:account_id>', methods=['POST'])
@login_required
def delete_account(account_id):
    """حذف حساب"""
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM instagram_accounts WHERE id = ?', (account_id,))
    conn.commit()
    conn.close()
    flash('تم حذف الحساب', 'info')
    return redirect(url_for('accounts'))

@app.route('/follows')
@login_required
def follows():
    """صفحة الرشقات"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM follows ORDER BY created_at DESC')
    follows_list = c.fetchall()
    conn.close()
    
    return render_template('follows.html', 
                         site_name=SITE_NAME,
                         follows=follows_list,
                         price_per_1000=PRICE_PER_1000)

@app.route('/start_follow', methods=['POST'])
@login_required
def start_follow():
    """بدء رشقة جديدة"""
    try:
        target = request.form.get('target')
        count = int(request.form.get('count'))
        
        # التحقق من وجود حسابات كافية
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM instagram_accounts WHERE status = 'active'")
        active_count = c.fetchone()[0]
        conn.close()
        
        if active_count < 1:
            flash('❌ لا يوجد حسابات نشطة', 'danger')
            return redirect(url_for('follows'))
        
        # إنشاء رشقة
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            INSERT INTO follows (target_username, total_count, status, created_at)
            VALUES (?, ?, ?, ?)
        ''', (target, count, 'pending', datetime.datetime.now().isoformat()))
        follow_id = c.lastrowid
        conn.commit()
        conn.close()
        
        add_log('follow_started', f'بدء رشقة لـ {target} - {count} متابع')
        flash(f'✅ تم بدء رشقة لـ {target} بـ {count} متابع', 'success')
        
        # بدء التنفيذ في الخلفية
        threading.Thread(target=execute_follow, args=(follow_id,)).start()
        
    except Exception as e:
        flash(f'❌ خطأ: {str(e)}', 'danger')
        logger.error(f"{Fore.RED}خطأ في بدء الرشقة: {e}{Style.RESET_ALL}")
    
    return redirect(url_for('follows'))

def execute_follow(follow_id):
    """تنفيذ الرشقة (في خلفية الموقع)"""
    try:
        # جلب بيانات الرشقة
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM follows WHERE id = ?', (follow_id,))
        follow = c.fetchone()
        conn.close()
        
        if not follow:
            return
        
        # تحديث الحالة
        conn = get_db()
        c = conn.cursor()
        c.execute('UPDATE follows SET status = ?, started_at = ? WHERE id = ?', 
                 ('running', datetime.datetime.now().isoformat(), follow_id))
        conn.commit()
        conn.close()
        
        total = follow['total_count']
        target = follow['target_username']
        
        # التوزيع على يومين
        day1_target = total // 2
        day2_target = total - day1_target
        
        # تنفيذ اليوم الأول
        completed = 0
        failed = 0
        
        # الحصول على حسابات نشطة
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM instagram_accounts WHERE status = 'active' ORDER BY last_used ASC")
        accounts = c.fetchall()
        conn.close()
        
        # تنفيذ المتابعات
        for i, account in enumerate(accounts):
            if completed >= day1_target:
                break
            
            # تسجيل الدخول
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            client = loop.run_until_complete(instagram_manager.login_account(account['id']))
            loop.close()
            
            if not client:
                continue
            
            # متابعة
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            success = loop.run_until_complete(instagram_manager.follow_user(client, target, account['id']))
            loop.close()
            
            if success:
                completed += 1
                conn = get_db()
                c = conn.cursor()
                c.execute('INSERT INTO follow_details (follow_id, account_id, success, created_at) VALUES (?, ?, ?, ?)',
                         (follow_id, account['id'], 1, datetime.datetime.now().isoformat()))
                conn.commit()
                conn.close()
            else:
                failed += 1
            
            # تأخير عشوائي
            time.sleep(random.randint(5, 15))
        
        # تحديث الإحصائيات
        conn = get_db()
        c = conn.cursor()
        if completed >= total:
            c.execute('UPDATE follows SET status = ?, completed_at = ?, completed_count = ?, failed_count = ? WHERE id = ?',
                     ('completed', datetime.datetime.now().isoformat(), completed, failed, follow_id))
        else:
            c.execute('UPDATE follows SET status = ?, day1_count = ?, current_day = ? WHERE id = ?',
                     ('pending', completed, 2, follow_id))
        conn.commit()
        conn.close()
        
        add_log('follow_executed', f'تم تنفيذ رشقة {follow_id}: {completed} نجاح، {failed} فشل')
        
    except Exception as e:
        logger.error(f"{Fore.RED}خطأ في تنفيذ الرشقة: {e}{Style.RESET_ALL}")
        conn = get_db()
        c = conn.cursor()
        c.execute('UPDATE follows SET status = ? WHERE id = ?', ('failed', follow_id))
        conn.commit()
        conn.close()

@app.route('/settings')
@login_required
def settings():
    """صفحة الإعدادات"""
    settings_dict = {}
    keys = ['min_delay', 'max_delay', 'daily_follow_limit', 'accounts_per_proxy', 'auto_create_accounts', 'retry_count']
    for key in keys:
        settings_dict[key] = get_setting(key, '')
    
    return render_template('settings.html', 
                         site_name=SITE_NAME,
                         settings=settings_dict)

@app.route('/update_settings', methods=['POST'])
@login_required
def update_settings():
    """تحديث الإعدادات"""
    try:
        keys = ['min_delay', 'max_delay', 'daily_follow_limit', 'accounts_per_proxy', 'auto_create_accounts', 'retry_count']
        for key in keys:
            value = request.form.get(key)
            if value:
                set_setting(key, value)
        
        flash('✅ تم تحديث الإعدادات بنجاح', 'success')
        add_log('settings_updated', 'تم تحديث الإعدادات')
    except Exception as e:
        flash(f'❌ خطأ: {str(e)}', 'danger')
    
    return redirect(url_for('settings'))

@app.route('/logs')
@login_required
def logs():
    """عرض السجلات"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM logs ORDER BY created_at DESC LIMIT 100')
    logs_list = c.fetchall()
    conn.close()
    
    return render_template('logs.html', 
                         site_name=SITE_NAME,
                         logs=logs_list)

@app.route('/api/status')
@login_required
def api_status():
    """API للحالة"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*) FROM instagram_accounts WHERE status = "active"')
    active_accounts = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM follows WHERE status = "pending"')
    pending_follows = c.fetchone()[0]
    
    conn.close()
    
    return jsonify({
        'active_accounts': active_accounts,
        'pending_follows': pending_follows,
        'version': VERSION
    })

# ------------------------------------------------------------------
# تشغيل التطبيق
# ------------------------------------------------------------------
if __name__ == '__main__':
    # تهيئة قاعدة البيانات
    init_db()
    
    # تشغيل الموقع
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"{Fore.CYAN}🚀 تشغيل موقع {SITE_NAME} الإصدار {VERSION}{Style.RESET_ALL}")
    logger.info(f"{Fore.GREEN}✅ يمكنك الوصول عبر http://localhost:{port}{Style.RESET_ALL}")
    
    app.run(debug=False, host='0.0.0.0', port=port)
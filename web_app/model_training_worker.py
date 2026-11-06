"""One bounded CPU worker with a PostgreSQL advisory lease and durable run states."""
from contextlib import asynccontextmanager
import hashlib
import io
from pathlib import Path
import shutil
import threading

import joblib
from psycopg.types.json import Jsonb

from web_app import model_training_math as math,model_training_store as store
from web_app.project_store import audit

ROOT=Path(__file__).resolve().parents[1]


class TrainingService:
    def __init__(self,database,root=None):
        self.database=database;self.root=Path(root or ROOT)
        self.lock=threading.Lock();self.event=threading.Event();self.stopping=threading.Event();self.thread=None;self.users=0

    def enter(self):
        with self.lock:self.users+=1
        self.kick()

    def leave(self):
        with self.lock:
            self.users-=1;last=self.users==0
        if last:
            self.stopping.set();self.event.set()
            if self.thread:self.thread.join(timeout=2)

    def kick(self):
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.stopping.clear()
                self.thread=threading.Thread(target=self.loop,name='coursework-ml-worker',daemon=True)
                self.thread.start()
            self.event.set()

    def loop(self):
        while not self.stopping.is_set():
            self.event.wait();self.event.clear()
            if self.stopping.is_set():break
            try:self.drain()
            except Exception:
                # A disconnected database must not expose credentials or leave a busy retry loop.
                print('Фоновое обучение: связь с БД прервана. Обновите страницу моделей после восстановления PostgreSQL.',flush=True)

    def drain(self):
        with self.database.connect() as lease:
            if lease.execute("SELECT to_regclass('web_training_runs') AS t").fetchone()['t'] is None:return
            key=lease.execute("SELECT hashtextextended(current_schema()||':web-model-worker',0) AS k").fetchone()['k']
            if not lease.execute('SELECT pg_try_advisory_lock(%s) AS ok',(key,)).fetchone()['ok']:return
            try:
                # Only the lease holder can train; old running states therefore belong to a stopped process.
                with self.database.connect() as connection:
                    old=connection.execute("SELECT id FROM web_training_runs WHERE status='running'").fetchall()
                    connection.execute("UPDATE web_training_runs SET status='interrupted',progress='Сервер был остановлен',error='Предыдущий процесс не завершил обучение. Запустите новый опыт.',finished_at=now() WHERE status='running'")
                for row in old:self.cleanup(str(row['id']))
                while not self.stopping.is_set():
                    with self.database.connect() as connection:
                        run=connection.execute("SELECT * FROM web_training_runs WHERE status='queued' ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
                        if not run:break
                        if run['cancel_requested']:
                            connection.execute("UPDATE web_training_runs SET status='cancelled',progress='Отменено до запуска',finished_at=now() WHERE id=%s",(run['id'],))
                            continue
                        connection.execute("UPDATE web_training_runs SET status='running',progress='Подготовка данных',started_at=now() WHERE id=%s",(run['id'],))
                    self.execute(dict(run))
            finally:lease.execute('SELECT pg_advisory_unlock(%s)',(key,))

    def progress(self,run,message):
        if self.stopping.is_set():raise InterruptedError('Сервер останавливается.')
        with self.database.connect() as connection:
            row=connection.execute('SELECT status,cancel_requested FROM web_training_runs WHERE id=%s',(run['id'],)).fetchone()
            if not row or row['cancel_requested'] or row['status']!='running':raise InterruptedError('Обучение отменено.')
            if not store.permitted(connection,run['project_id'],run['created_by']):raise ValueError('У инициатора больше нет действующего доступа аналитика.')
            connection.execute('UPDATE web_training_runs SET progress=%s WHERE id=%s',(message,run['id']))

    def execute(self,run):
        rid=str(run['id']);committed=False
        try:
            self.progress(run,'Групповое разделение 80/20')
            records=run['input_snapshot']['records']
            if store.digest(records)!=run['input_snapshot']['sha256']:raise ValueError('Снимок исходных данных не соответствует контрольной сумме.')
            result=math.train(records,run['params']['seed'],lambda message:self.progress(run,message))
            self.progress(run,'Сохранение двух версий моделей')
            directory=self.root/'models'/'web'/run['id'].hex
            base=(self.root/'models').resolve()
            if not directory.resolve().is_relative_to(base):raise ValueError('Каталог артефактов недоступен.')
            directory.mkdir(parents=True,exist_ok=False)
            artifacts={}
            for name,model in result['models'].items():
                buffer=io.BytesIO();joblib.dump(model['pipeline'],buffer,compress=3);data=buffer.getvalue()
                path=directory/(name+'.joblib')
                with path.open('xb') as handle:handle.write(data)
                artifacts[name]={'path':path.relative_to(self.root).as_posix(),'sha256':hashlib.sha256(data).hexdigest()}
            self.progress(run,'Регистрация моделей в PostgreSQL')
            with self.database.connect() as connection:store.persist(connection,run,result,artifacts)
            committed=True
        except Exception as exc:
            try:
                with self.database.connect() as connection:
                    row=connection.execute('SELECT status,cancel_requested FROM web_training_runs WHERE id=%s FOR UPDATE',(rid,)).fetchone()
                    if row and row['status']=='running':
                        status=('cancelled' if row['cancel_requested'] else 'interrupted') if isinstance(exc,InterruptedError) else 'failed'
                        error=str(exc)[:500] if isinstance(exc,(ValueError,InterruptedError)) else 'Ошибка обучения или сохранения. Проверьте доступ к БД и каталогу models/web, затем повторите запуск.'
                        connection.execute('UPDATE web_training_runs SET status=%s,progress=%s,error=%s,finished_at=now() WHERE id=%s',(status,'Обучение остановлено',error,rid))
                        audit(connection,run['project_id'],run['created_by'],'web_model_training_'+status,{'run_id':rid,'error':error})
            except Exception:pass  # Recovery on the next advisory-lease acquisition records interruption.
        finally:
            if not committed:self.cleanup(rid)

    def cleanup(self,rid):
        import uuid
        directory=self.root/'models'/'web'/uuid.UUID(rid).hex
        try:
            with self.database.connect() as connection:
                referenced=connection.execute('SELECT 1 FROM ml_models WHERE artifact_path LIKE %s LIMIT 1',('models/web/'+uuid.UUID(rid).hex+'/%',)).fetchone()
            if not referenced and directory.exists() and not directory.is_symlink():shutil.rmtree(directory)
        except Exception:pass


def install_lifespan(app,service):
    previous=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous(application):
            service.enter()
            try:yield
            finally:service.leave()
    app.router.lifespan_context=lifespan

"""
Celery Configuration for EMOG
Async task processing for heavy LLM operations, memory indexing, and graph operations.
"""
import os
from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready, task_failure, task_success
from kombu import Queue

# Celery app
app = Celery('emog')

# Configuration
app.conf.update(
    # Broker
    broker_url=os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/1'),
    result_backend=os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/2'),
    
    # Serialization
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    
    # Timezone
    timezone='UTC',
    enable_utc=True,
    
    # Task routing
    task_routes={
        'agent_app.tasks.memory.index_episodic': {'queue': 'memory'},
        'agent_app.tasks.memory.index_semantic': {'queue': 'memory'},
        'agent_app.tasks.memory.prune': {'queue': 'maintenance'},
        'agent_app.tasks.llm.ensemble': {'queue': 'llm'},
        'agent_app.tasks.llm.single': {'queue': 'llm'},
        'agent_app.tasks.projects.*': {'queue': 'projects'},
    },
    
    # Queue definitions
    task_queues=(
        Queue('llm', routing_key='llm'),
        Queue('memory', routing_key='memory'),
        Queue('projects', routing_key='projects'),
        Queue('maintenance', routing_key='maintenance'),
        Queue('default', routing_key='default'),
    ),
    
    # Worker settings
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_max_tasks_per_child=100,
    
    # Task time limits
    task_soft_time_limit=300,  # 5 minutes
    task_time_limit=360,       # 6 minutes
    
    # Result expiration
    result_expires=3600,  # 1 hour
    
    # Beat schedule (celery-beat)
    beat_schedule={
        'memory-pruning-daily': {
            'task': 'agent_app.tasks.memory.prune',
            'schedule': crontab(hour=3, minute=0),  # 3 AM daily
        },
        'episodic-reindex-weekly': {
            'task': 'agent_app.tasks.memory.reindex_episodic',
            'schedule': crontab(hour=4, minute=0, day_of_week=0),  # Sunday 4 AM
        },
        'semantic-dedup-weekly': {
            'task': 'agent_app.tasks.memory.deduplicate_semantic',
            'schedule': crontab(hour=5, minute=0, day_of_week=0),  # Sunday 5 AM
        },
    },
    
    # Monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,
)

# Auto-discover tasks
app.autodiscover_tasks([
    'agent_app.tasks',
])


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')


# Signals
@worker_ready.connect
def worker_ready_handler(sender=None, **kwargs):
    print(f'Worker {sender} ready')


@task_failure.connect
def task_failure_handler(sender=None, exception=None, **kwargs):
    print(f'Task {sender.name} failed: {exception}')


@task_success.connect
def task_success_handler(sender=None, result=None, **kwargs):
    pass  # Could log success metrics here
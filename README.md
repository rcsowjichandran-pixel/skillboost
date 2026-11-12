# SkillBoost LMS

## Setup on Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
$env:DJANGO_SECRET_KEY = python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
$env:DJANGO_DEBUG = "true"
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000/admin/` to manage courses, lessons, quizzes,
questions, answers, attempts, and course completions. SQLite is used by default
so the project runs locally without a database server.

The Docker Compose service collects static files when it starts. WhiteNoise
serves them even when `DJANGO_DEBUG=false`, and hashed stylesheet URLs make
browsers request updated CSS after each change. The Docker image also collects
static files before starting Gunicorn. For other deployment methods, run
`python manage.py collectstatic --noinput` as part of each deployment.

To use MySQL, create the database and set these environment variables before
running Django commands:

```powershell
$env:DB_ENGINE = "mysql"
$env:DB_NAME = "skillboost"
$env:DB_USER = "your_mysql_user"
$env:DB_PASSWORD = "your_mysql_password"
$env:DB_HOST = "127.0.0.1"
$env:DB_PORT = "3306"
```

Quiz generation also requires a Gemini API key:

```powershell
$env:GEMINI_API_KEY = "your_api_key"
```

For Docker Compose, put your actual key in the project-root `.env` file as
`GEMINI_API_KEY=...` (do not use the example placeholder), then recreate the
web service so it receives the updated environment:

```powershell
docker compose up -d --build --force-recreate web
```

Get a key from [Google AI Studio](https://aistudio.google.com/app/apikey). Keep
it private and do not commit it.

Local Django commands load values from the project-root `.env` file
automatically. Restart the Django server after changing `.env`. Docker Compose
also passes that file to the web container; rebuild and recreate the container
after changing dependencies or configuration:

```powershell
docker compose up -d --build --force-recreate web
```

Learners sign in at `/accounts/login/`, take quizzes, and receive course
completion records after scoring at least 80%. A completed learner can download
their course certificate from the course page. The home page recommends
available courses based on completed courses and recent quiz results; this
feature requires `GEMINI_API_KEY` and sends that activity and the course catalog
to Gemini.

The public course catalog is available at `/` and lists every course with links
to its lessons. Learners can register at `/accounts/signup/`; registration asks
for a first and last name and signs the learner in so they can continue to the
requested quiz. Existing learners can update their certificate name from
**My profile**. Only staff accounts can create courses or add lessons; staff
can sign in through `/admin/`.
Certificates are landscape PDFs with the learner's name, course, completion
date, issuer, and a unique certificate ID. A learner must pass a quiz with at
least 80% to complete the course and download its certificate.
Lesson video URLs should point directly to browser-playable video files such as
MP4 or WebM. Videos play on the lesson page; when playback ends, the player
closes and the quiz option is brought into view.

Run the LMS tests with:

```powershell
python manage.py test lms
```

For deployment, set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`, and
`DJANGO_ALLOWED_HOSTS`; Django refuses to start without a secret key. Set
`DJANGO_SECURE_SSL_REDIRECT=true` when HTTPS is configured at the application
or proxy. `.env.example` documents environment variables; copy it to `.env` and replace
the example values before running the application.

Apply migrations and collect static files before deployment with:
`python manage.py migrate --noinput && python manage.py collectstatic --noinput`
Gunicorn is included as the production WSGI server for Linux/macOS:

```sh
gunicorn skillboost.wsgi:application
```

Gunicorn is not compatible with native Windows; use a Linux deployment
environment (or WSL) to run that command.

import asyncio
import json
import logging
import os
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from asgiref.sync import async_to_sync
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, transaction
from django.http import FileResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST
from django.urls import reverse
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
import reportlab

from .forms import (
    CourseForm,
    LearnerProfileForm,
    LearnerSignupForm,
    VideoLessonForm,
)
from .models import (
    Answer,
    Course,
    CourseCompletion,
    Question,
    Quiz,
    QuizAttempt,
    VideoLesson,
)


logger = logging.getLogger(__name__)
PASSING_PERCENTAGE = 80


@require_http_methods(["GET", "POST"])
@staff_member_required
def course_upload(request):
    if request.method == "POST":
        form = CourseForm(request.POST)
        if form.is_valid():
            course = form.save()
            messages.success(request, "Course created successfully.")
            return redirect("lms:course_detail", course_id=course.pk)
    else:
        form = CourseForm()
    return render(request, "lms/course_upload.html", {"form": form})


@require_http_methods(["GET", "POST"])
@staff_member_required
def video_lesson_upload(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    if request.method == "POST":
        form = VideoLessonForm(request.POST)
        if form.is_valid():
            lesson = form.save(commit=False)
            lesson.course = course
            lesson.save()
            messages.success(request, "Video lesson created successfully.")
            return redirect("lms:lesson_detail", lesson_id=lesson.pk)
    else:
        form = VideoLessonForm()
    return render(
        request,
        "lms/video_lesson_upload.html",
        {"form": form, "course": course},
    )


def course_detail(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    completion = None
    if request.user.is_authenticated:
        completion = CourseCompletion.objects.filter(
            user=request.user, course=course, is_completed=True
        ).first()
    return render(
        request,
        "lms/course_detail.html",
        {
            "course": course,
            "lessons": course.lessons.all(),
            "is_completed": completion is not None,
        },
    )


def course_catalog(request):
    courses = Course.objects.order_by("title")
    return render(request, "lms/course_catalog.html", {"courses": courses})


def _safe_next_url(request):
    next_url = request.POST.get("next") or request.GET.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return ""


@require_http_methods(["GET", "POST"])
def learner_signup(request):
    if request.user.is_authenticated:
        return redirect("lms:course_catalog")

    next_url = _safe_next_url(request)
    if request.method == "POST":
        form = LearnerSignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Your learner account is ready. Welcome!")
            return redirect(next_url or "lms:course_catalog")
    else:
        form = LearnerSignupForm()
    return render(
        request,
        "registration/signup.html",
        {"form": form, "next": next_url},
    )


@login_required
@require_http_methods(["GET", "POST"])
def learner_profile(request):
    next_url = _safe_next_url(request)
    if request.method == "POST":
        form = LearnerProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your profile and certificate name are updated.")
            return redirect(next_url or "lms:course_catalog")
    else:
        form = LearnerProfileForm(instance=request.user)
    return render(
        request,
        "registration/profile.html",
        {"form": form, "next": next_url},
    )


@login_required
def recommend_courses(request):
    completed_courses = list(
        CourseCompletion.objects.filter(user=request.user, is_completed=True)
        .select_related("course")
        .order_by("-completed_at")[:10]
    )
    recent_attempts = list(
        QuizAttempt.objects.filter(user=request.user)
        .select_related("quiz", "quiz__course")
        .order_by("-created_at")[:10]
    )
    completed_course_ids = CourseCompletion.objects.filter(
        user=request.user, is_completed=True
    ).values("course_id")
    courses = list(Course.objects.exclude(pk__in=completed_course_ids).order_by("title"))
    if not courses:
        return render(
            request,
            "lms/recommendations.html",
            {"recommendations": [], "no_available_courses": True},
        )

    api_key = os.environ.get("GEMINI_API_KEY")
    activity = {
        "completed_courses": [
            {
                "title": completion.course.title,
                "completed_at": (
                    completion.completed_at.isoformat()
                    if completion.completed_at
                    else None
                ),
            }
            for completion in completed_courses
        ],
        "recent_quiz_results": [
            {
                "course": attempt.quiz.course.title,
                "quiz": attempt.quiz.title,
                "score": attempt.score,
                "created_at": attempt.created_at.isoformat(),
            }
            for attempt in recent_attempts
        ],
    }
    catalog = [
        {"title": course.title, "description": course.description}
        for course in courses
    ]
    recommendations = []
    if api_key:
        try:
            response = async_to_sync(_request_gemini_recommendations)(
                activity, catalog, api_key
            )
            generated = _extract_recommendations(response)
        except (
            HTTPError,
            URLError,
            TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            logger.exception("Gemini course recommendation request failed")
            messages.warning(
                request,
                "Personalized recommendations are temporarily unavailable. "
                "Showing available courses instead.",
            )
        except ValueError:
            logger.exception("Gemini returned invalid course recommendations")
            messages.warning(
                request,
                "Personalized recommendations could not be processed. "
                "Showing available courses instead.",
            )
        else:
            courses_by_title = {
                course.title.strip().casefold(): course for course in courses
            }
            for recommendation in generated:
                course = courses_by_title.get(
                    recommendation["title"].strip().casefold()
                )
                if course:
                    recommendations.append(
                        {
                            **recommendation,
                            "course": course,
                            "personalized": True,
                        }
                    )
            recommendations.sort(
                key=lambda item: item["relevance_score"], reverse=True
            )
            if not recommendations:
                messages.warning(
                    request,
                    "No personalized recommendations matched the available courses. "
                    "Showing available courses instead.",
                )
    else:
        messages.warning(
            request,
            "Set GEMINI_API_KEY to enable personalized recommendations. "
            "Showing available courses instead.",
        )

    if not recommendations:
        recommendations = [
            {
                "title": course.title,
                "description": course.description,
                "relevance_score": 0.0,
                "course": course,
                "personalized": False,
            }
            for course in courses[:5]
        ]
    return render(
        request,
        "lms/recommendations.html",
        {
            "recommendations": recommendations,
            "no_available_courses": False,
        },
    )


def lesson_detail(request, lesson_id):
    lesson = get_object_or_404(
        VideoLesson.objects.select_related("course"), pk=lesson_id
    )
    video_path = urlsplit(lesson.video_url).path.lower()
    video_is_direct = video_path.endswith((".mp4", ".webm", ".ogv"))
    return render(
        request,
        "lms/video_lesson_detail.html",
        {
            "lesson": lesson,
            "quizzes": lesson.quizzes.all(),
            "video_is_direct": video_is_direct,
        },
    )


@login_required
@require_http_methods(["GET"])
def quiz_take(request, quiz_id):
    quiz = get_object_or_404(
        Quiz.objects.prefetch_related("questions__answers"), pk=quiz_id
    )
    return render(request, "lms/quiz_take.html", {"quiz": quiz})


@login_required
@require_POST
def quiz_submit(request, quiz_id):
    quiz = get_object_or_404(Quiz.objects.select_related("course"), pk=quiz_id)
    questions = list(quiz.questions.prefetch_related("answers"))
    if not questions:
        return HttpResponseBadRequest("This quiz does not contain any questions.")

    correct_count = 0
    answer_results = []
    for question in questions:
        answers = list(question.answers.all())
        selected_id = request.POST.get(str(question.pk))
        selected_answer = next(
            (answer for answer in answers if str(answer.pk) == selected_id), None
        )
        correct_answers = [answer for answer in answers if answer.is_correct]
        is_correct = selected_answer is not None and selected_answer.is_correct
        correct_count += int(is_correct)
        answer_results.append(
            {
                "question": question.question_text,
                "selected_answer": (
                    selected_answer.answer_text if selected_answer else None
                ),
                "correct_answers": [
                    answer.answer_text for answer in correct_answers
                ],
                "is_correct": is_correct,
            }
        )

    total_questions = len(questions)
    score = round(correct_count * 100 / total_questions)
    with transaction.atomic():
        attempt = QuizAttempt.objects.create(
            user=request.user,
            quiz=quiz,
            score=score,
            correct_answers=correct_count,
            total_questions=total_questions,
            answer_results=answer_results,
        )
        if correct_count * 100 >= PASSING_PERCENTAGE * total_questions:
            completion, _ = CourseCompletion.objects.get_or_create(
                user=request.user,
                course=quiz.course,
                defaults={"is_completed": True, "completed_at": timezone.now()},
            )
            if not completion.is_completed:
                completion.is_completed = True
                completion.completed_at = timezone.now()
                completion.save(update_fields=["is_completed", "completed_at"])

    return redirect("lms:quiz_results", attempt_id=attempt.pk)


@login_required
def quiz_results(request, attempt_id):
    attempt = get_object_or_404(
        QuizAttempt.objects.select_related("quiz", "quiz__course"),
        pk=attempt_id,
        user=request.user,
    )
    return render(request, "lms/quiz_results.html", {"attempt": attempt})


@login_required
def generate_certificate(request, course_id, user_id):
    if request.user.pk != user_id:
        raise PermissionDenied
    completion = get_object_or_404(
        CourseCompletion.objects.select_related("course"),
        user=request.user,
        course_id=course_id,
        is_completed=True,
        completed_at__isnull=False,
    )

    full_name = request.user.get_full_name().strip()
    if not full_name:
        messages.info(
            request,
            "Add your first and last name to your profile before downloading "
            "your certificate.",
        )
        return redirect(
            f"{reverse('lms:learner_profile')}?next={request.path}"
        )

    buffer = BytesIO()
    page_size = landscape(letter)
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    width, height = page_size
    font_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    if "SkillBoostVera" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont("SkillBoostVera", str(font_dir / "Vera.ttf"))
        )
        pdfmetrics.registerFont(
            TTFont("SkillBoostVera-Bold", str(font_dir / "VeraBd.ttf"))
        )

    green = colors.HexColor("#14563f")
    gold = colors.HexColor("#c7953e")
    certificate_number = f"SB-{completion.pk:08d}"
    pdf.setTitle(f"{completion.course.title} certificate")
    pdf.setFillColor(colors.white)
    pdf.rect(0, 0, width, height, stroke=0, fill=1)
    pdf.setStrokeColor(green)
    pdf.setLineWidth(4)
    pdf.roundRect(24, 24, width - 48, height - 48, 16, stroke=1, fill=0)
    pdf.setStrokeColor(gold)
    pdf.setLineWidth(1)
    pdf.roundRect(33, 33, width - 66, height - 66, 12, stroke=1, fill=0)

    pdf.setFillColor(green)
    pdf.setFont("SkillBoostVera-Bold", 13)
    pdf.drawCentredString(width / 2, height - 83, "SKILLBOOST LEARNING")
    pdf.setFillColor(gold)
    pdf.setFont("SkillBoostVera-Bold", 27)
    pdf.drawCentredString(width / 2, height - 137, "Certificate of Completion")

    seal_x, seal_y = width - 105, height - 105
    pdf.setFillColor(green)
    pdf.circle(seal_x, seal_y, 27, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont("SkillBoostVera-Bold", 17)
    pdf.drawCentredString(seal_x, seal_y - 6, "S")
    pdf.setFillColor(colors.HexColor("#58675e"))
    pdf.setFont("SkillBoostVera", 13)
    pdf.drawCentredString(width / 2, height - 185, "This certificate is proudly presented to")

    name_size = min(
        29,
        29
        * width
        * 0.72
        / pdfmetrics.stringWidth(full_name, "SkillBoostVera-Bold", 29),
    )
    pdf.setFillColor(green)
    pdf.setFont("SkillBoostVera-Bold", name_size)
    pdf.drawCentredString(width / 2, height - 233, full_name)
    pdf.setStrokeColor(gold)
    pdf.setLineWidth(1.2)
    pdf.line(width * 0.25, height - 247, width * 0.75, height - 247)

    course_title = completion.course.title
    course_size = min(
        22,
        22
        * width
        * 0.74
        / pdfmetrics.stringWidth(course_title, "SkillBoostVera-Bold", 22),
    )
    pdf.setFillColor(colors.HexColor("#58675e"))
    pdf.setFont("SkillBoostVera", 12)
    pdf.drawCentredString(width / 2, height - 279, "for successfully completing the course")
    pdf.setFillColor(green)
    pdf.setFont("SkillBoostVera-Bold", course_size)
    pdf.drawCentredString(width / 2, height - 316, course_title)

    pdf.setFillColor(colors.HexColor("#58675e"))
    pdf.setFont("SkillBoostVera", 10)
    completed_date = timezone.localtime(completion.completed_at).strftime(
        "%B %d, %Y"
    )
    pdf.drawCentredString(width / 2, 112, f"Completed on {completed_date}")
    pdf.setStrokeColor(green)
    pdf.line(95, 78, 260, 78)
    pdf.line(width - 260, 78, width - 95, 78)
    pdf.setFont("SkillBoostVera-Bold", 9)
    pdf.drawCentredString(177, 62, "SKILLBOOST LMS")
    pdf.setFont("SkillBoostVera", 9)
    pdf.drawCentredString(width - 177, 62, "COURSE COMPLETION")
    pdf.setFont("SkillBoostVera", 8)
    pdf.drawCentredString(width / 2, 43, f"Certificate ID: {certificate_number}")
    pdf.save()
    buffer.seek(0)
    return FileResponse(
        buffer,
        as_attachment=True,
        filename=f"skillboost-certificate-{certificate_number.lower()}.pdf",
    )


async def _request_gemini_quiz(description, api_key):
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.6-flash:generateContent?"
        + urlencode({"key": api_key})
    )
    schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "question": {"type": "STRING"},
                "options": {"type": "ARRAY", "items": {"type": "STRING"}},
                "correctAnswer": {"type": "STRING"},
            },
            "required": ["question", "options", "correctAnswer"],
        },
    }
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Act as a quiz generator. Read the lesson description "
                            "and create 5 multiple-choice questions based only on "
                            "its content. Return a JSON array. Each object must "
                            "contain question (string), options (array of strings), "
                            "and correctAnswer (string matching exactly one option). "
                            "Do not include markdown.\n\nLesson description:\n"
                            f"{description}"
                        )
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    def send_request():
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    return await asyncio.to_thread(send_request)


async def _request_gemini_recommendations(activity, catalog, api_key):
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.6-flash:generateContent"
    )
    schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING"},
                "description": {"type": "STRING"},
                "relevance_score": {"type": "NUMBER"},
            },
            "required": ["title", "description", "relevance_score"],
        },
    }
    prompt = (
        "Recommend up to 5 courses to this learner. Use their completed-course "
        "history and recent quiz results to personalize your reasoning. Recommend "
        "only courses from the available course catalog, use each catalog title "
        "exactly, and do not recommend courses already completed. Return a JSON "
        "array; each item must contain title (string), description (string), and "
        "relevance_score (number from 0 to 1). Do not include markdown.\n\n"
        f"Learner activity:\n{json.dumps(activity, ensure_ascii=False)}\n\n"
        f"Available course catalog:\n{json.dumps(catalog, ensure_ascii=False)}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )

    def send_request():
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    return await asyncio.to_thread(send_request)


def _extract_questions(response):
    try:
        text = response["candidates"][0]["content"]["parts"][0]["text"]
        questions = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("The AI returned an invalid quiz response.") from exc
    if not isinstance(questions, list) or not questions:
        raise ValueError("The AI returned no quiz questions.")

    for item in questions:
        if not isinstance(item, dict):
            raise ValueError("The AI returned a malformed question.")
        question = item.get("question")
        options = item.get("options")
        correct_answer = item.get("correctAnswer")
        if (
            not isinstance(question, str)
            or not question.strip()
            or not isinstance(options, list)
            or len(options) < 2
            or not all(isinstance(option, str) and option.strip() for option in options)
            or len(set(options)) != len(options)
            or not isinstance(correct_answer, str)
            or correct_answer not in options
        ):
            raise ValueError("The AI returned a malformed question or answer.")
    return questions


def _extract_recommendations(response):
    try:
        text = response["candidates"][0]["content"]["parts"][0]["text"]
        recommendations = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("The AI returned an invalid recommendation response.") from exc
    if not isinstance(recommendations, list):
        raise ValueError("The AI returned an invalid recommendation list.")

    for item in recommendations:
        if not isinstance(item, dict):
            raise ValueError("The AI returned a malformed recommendation.")
        title = item.get("title")
        description = item.get("description")
        relevance_score = item.get("relevance_score")
        if (
            not isinstance(title, str)
            or not title.strip()
            or not isinstance(description, str)
            or not description.strip()
            or isinstance(relevance_score, bool)
            or not isinstance(relevance_score, (int, float))
            or not 0 <= relevance_score <= 1
        ):
            raise ValueError("The AI returned a malformed recommendation.")
    return recommendations


@login_required
@require_POST
def generate_quiz(request, lesson_id):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        messages.error(request, "Quiz generation is not configured. Set GEMINI_API_KEY.")
        return redirect("lms:lesson_detail", lesson_id=lesson_id)

    lesson = get_object_or_404(VideoLesson.objects.select_related("course"), pk=lesson_id)
    try:
        response = async_to_sync(_request_gemini_quiz)(lesson.description, api_key)
        questions = _extract_questions(response)
    except HTTPError as exc:
        logger.warning("Gemini quiz generation returned HTTP %s", exc.code)
        if exc.code == 429:
            messages.error(
                request,
                "Gemini's quota or rate limit was reached. Check your Google AI "
                "Studio project quota and try again later.",
            )
        elif exc.code == 503:
            messages.error(
                request,
                "Gemini is temporarily unavailable (HTTP 503). Please wait a "
                "minute and try generating the quiz again.",
            )
        else:
            messages.error(
                request,
                f"Gemini could not generate the quiz (HTTP {exc.code}). "
                "Check your API key and model access.",
            )
        return redirect("lms:lesson_detail", lesson_id=lesson.pk)
    except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        logger.exception("Gemini quiz generation request failed")
        messages.error(
            request, "The quiz service could not be reached. Please try again later."
        )
        return redirect("lms:lesson_detail", lesson_id=lesson.pk)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("lms:lesson_detail", lesson_id=lesson.pk)

    try:
        with transaction.atomic():
            quiz = Quiz.objects.create(
                course=lesson.course,
                lesson=lesson,
                title=f"{lesson.title} quiz",
            )
            for item in questions:
                question = Question.objects.create(
                    quiz=quiz, question_text=item["question"]
                )
                Answer.objects.bulk_create(
                    [
                        Answer(
                            question=question,
                            answer_text=option,
                            is_correct=option == item["correctAnswer"],
                        )
                        for option in item["options"]
                    ]
                )
    except DatabaseError:
        logger.exception("Generated quiz could not be saved")
        return JsonResponse({"error": "Quiz could not be saved."}, status=500)

    messages.success(request, "Quiz generated successfully.")
    return redirect("lms:lesson_detail", lesson_id=lesson.pk)

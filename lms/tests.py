import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Answer,
    Course,
    CourseCompletion,
    Question,
    Quiz,
    QuizAttempt,
    VideoLesson,
)
from .forms import CourseForm, VideoLessonForm


class QuizFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="learner", password="test-password"
        )
        self.user.first_name = "Ada"
        self.user.last_name = "Lovelace"
        self.user.save(update_fields=["first_name", "last_name"])
        self.course = Course.objects.create(
            title="Python basics", description="Learn Python."
        )
        self.lesson = VideoLesson.objects.create(
            course=self.course,
            title="Variables",
            video_url="https://example.com/variables.mp4",
            description="Variables store values.",
        )
        self.quiz = Quiz.objects.create(
            course=self.course, lesson=self.lesson, title="Variables quiz"
        )
        self.questions = [
            Question.objects.create(quiz=self.quiz, question_text=f"Question {index}")
            for index in range(1, 3)
        ]
        self.correct_answers = [
            Answer.objects.create(
                question=question,
                answer_text=f"Correct {index}",
                is_correct=True,
            )
            for index, question in enumerate(self.questions, start=1)
        ]
        self.incorrect_answers = [
            Answer.objects.create(
                question=question,
                answer_text=f"Incorrect {index}",
                is_correct=False,
            )
            for index, question in enumerate(self.questions, start=1)
        ]
        self.client.force_login(self.user)

    def test_passing_submission_saves_attempt_and_completes_course(self):
        response = self.client.post(
            reverse("lms:quiz_submit", args=[self.quiz.pk]),
            {
                str(self.questions[0].pk): str(self.correct_answers[0].pk),
                str(self.questions[1].pk): str(self.correct_answers[1].pk),
            },
        )

        attempt = QuizAttempt.objects.get(user=self.user, quiz=self.quiz)
        completion = CourseCompletion.objects.get(user=self.user, course=self.course)
        self.assertRedirects(
            response, reverse("lms:quiz_results", args=[attempt.pk])
        )
        self.assertEqual(attempt.score, 100)
        self.assertEqual(len(attempt.answer_results), 2)
        self.assertTrue(completion.is_completed)
        self.assertIsNotNone(completion.completed_at)

    def test_failing_submission_does_not_complete_course(self):
        response = self.client.post(
            reverse("lms:quiz_submit", args=[self.quiz.pk]),
            {
                str(self.questions[0].pk): str(self.correct_answers[0].pk),
                str(self.questions[1].pk): str(self.incorrect_answers[1].pk),
            },
        )

        attempt = QuizAttempt.objects.get(user=self.user, quiz=self.quiz)
        self.assertRedirects(
            response, reverse("lms:quiz_results", args=[attempt.pk])
        )
        self.assertEqual(attempt.score, 50)
        self.assertFalse(
            CourseCompletion.objects.filter(
                user=self.user, course=self.course, is_completed=True
            ).exists()
        )

    def test_unrelated_answer_is_not_graded_as_correct(self):
        another_quiz = Quiz.objects.create(
            course=self.course, title="Another quiz"
        )
        other_question = Question.objects.create(
            quiz=another_quiz, question_text="Other question"
        )
        unrelated_answer = Answer.objects.create(
            question=other_question, answer_text="Correct-looking", is_correct=True
        )

        self.client.post(
            reverse("lms:quiz_submit", args=[self.quiz.pk]),
            {
                str(self.questions[0].pk): str(unrelated_answer.pk),
                str(self.questions[1].pk): str(self.incorrect_answers[1].pk),
            },
        )

        attempt = QuizAttempt.objects.get(user=self.user, quiz=self.quiz)
        self.assertEqual(attempt.score, 0)

    def test_certificate_download_requires_completion(self):
        response = self.client.get(
            reverse(
                "lms:generate_certificate",
                args=[self.course.pk, self.user.pk],
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_completed_certificate_uses_learner_name_and_certificate_id(self):
        completion = CourseCompletion.objects.create(
            user=self.user,
            course=self.course,
            is_completed=True,
            completed_at=timezone.now(),
        )
        with patch("lms.views.canvas.Canvas") as canvas_factory:
            response = self.client.get(
                reverse(
                    "lms:generate_certificate",
                    args=[self.course.pk, self.user.pk],
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        drawn_strings = [
            call.args[2]
            for call in canvas_factory.return_value.drawCentredString.call_args_list
        ]
        self.assertIn("Ada Lovelace", drawn_strings)
        self.assertIn(f"Certificate ID: SB-{completion.pk:08d}", drawn_strings)

    def test_existing_completed_learner_without_name_is_sent_to_profile(self):
        self.user.first_name = ""
        self.user.last_name = ""
        self.user.save(update_fields=["first_name", "last_name"])
        CourseCompletion.objects.create(
            user=self.user,
            course=self.course,
            is_completed=True,
            completed_at=timezone.now(),
        )

        response = self.client.get(
            reverse(
                "lms:generate_certificate",
                args=[self.course.pk, self.user.pk],
            )
        )

        self.assertRedirects(
            response,
            f'{reverse("lms:learner_profile")}?next='
            f'{reverse("lms:generate_certificate", args=[self.course.pk, self.user.pk])}',
            fetch_redirect_response=False,
        )

    def test_completed_user_can_download_certificate(self):
        response = self.client.post(
            reverse("lms:quiz_submit", args=[self.quiz.pk]),
            {
                str(self.questions[0].pk): str(self.correct_answers[0].pk),
                str(self.questions[1].pk): str(self.correct_answers[1].pk),
            },
        )
        results_response = self.client.get(response.url)

        response = self.client.get(
            reverse(
                "lms:generate_certificate",
                args=[self.course.pk, self.user.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(results_response.status_code, 200)
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF"))
        self.assertTrue(
            CourseCompletion.objects.filter(
                user=self.user, course=self.course, is_completed=True
            ).exists()
        )

    def test_users_cannot_view_another_users_attempt_results(self):
        attempt = QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz,
            score=0,
            correct_answers=0,
            total_questions=2,
            answer_results=[],
        )
        other_user = get_user_model().objects.create_user(
            username="other", password="test-password"
        )
        self.client.force_login(other_user)

        response = self.client.get(
            reverse("lms:quiz_results", args=[attempt.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_completion_is_only_shown_to_the_completing_user(self):
        CourseCompletion.objects.create(
            user=self.user,
            course=self.course,
            is_completed=True,
            completed_at=timezone.now(),
        )
        response = self.client.get(
            reverse("lms:course_detail", args=[self.course.pk])
        )
        self.assertContains(response, "Course completed")
        self.assertContains(response, "Download certificate")

        other_user = get_user_model().objects.create_user(
            username="other", password="test-password"
        )
        self.client.force_login(other_user)
        response = self.client.get(
            reverse("lms:course_detail", args=[self.course.pk])
        )
        self.assertNotContains(response, "Course completed")
        self.assertNotContains(response, "Download certificate")

    def test_quiz_requires_login_and_login_page_renders(self):
        self.client.logout()

        response = self.client.get(
            reverse("lms:quiz_take", args=[self.quiz.pk])
        )

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('lms:quiz_take', args=[self.quiz.pk])}",
        )
        login_page = self.client.get(reverse("login"))
        self.assertEqual(login_page.status_code, 200)
        self.assertContains(login_page, "Create a learner account")

    def test_account_logout_redirects_to_the_styled_course_catalog(self):
        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, reverse("lms:course_catalog"))
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_admin_logout_redirects_to_the_styled_course_catalog(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])

        response = self.client.post("/admin/logout/")

        self.assertRedirects(response, reverse("lms:course_catalog"))
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_learner_can_sign_up_and_continue_to_quiz(self):
        self.client.logout()
        quiz_url = reverse("lms:quiz_take", args=[self.quiz.pk])

        response = self.client.post(
            f'{reverse("lms:learner_signup")}?next={quiz_url}',
            {
                "first_name": "Grace",
                "last_name": "Hopper",
                "username": "grace-hopper",
                "password1": "A-unique-learning-password-947!",
                "password2": "A-unique-learning-password-947!",
                "next": quiz_url,
            },
            follow=True,
        )

        learner = get_user_model().objects.get(username="grace-hopper")
        self.assertEqual(learner.get_full_name(), "Grace Hopper")
        self.assertFalse(learner.is_staff)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertEqual(response.redirect_chain[-1][0], quiz_url)
        self.assertContains(response, self.questions[0].question_text)

    def test_learner_signup_rejects_missing_name_and_external_redirect(self):
        self.client.logout()
        response = self.client.post(
            reverse("lms:learner_signup"),
            {
                "first_name": "",
                "last_name": "",
                "username": "nameless-learner",
                "password1": "A-unique-learning-password-947!",
                "password2": "A-unique-learning-password-947!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            get_user_model().objects.filter(username="nameless-learner").exists()
        )

        response = self.client.post(
            reverse("lms:learner_signup"),
            {
                "first_name": "Grace",
                "last_name": "Hopper",
                "username": "safe-learner",
                "password1": "A-unique-learning-password-947!",
                "password2": "A-unique-learning-password-947!",
                "next": "https://example.invalid/",
            },
        )
        self.assertRedirects(
            response, reverse("lms:course_catalog"), fetch_redirect_response=False
        )

    def test_learner_can_update_certificate_name_in_profile(self):
        response = self.client.post(
            reverse("lms:learner_profile"),
            {
                "first_name": "Katherine",
                "last_name": "Johnson",
                "email": "katherine@example.com",
            },
        )

        self.assertRedirects(response, reverse("lms:course_catalog"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), "Katherine Johnson")


    def test_quiz_page_renders_questions_and_answers(self):
        response = self.client.get(reverse("lms:quiz_take", args=[self.quiz.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.questions[0].question_text)
        self.assertContains(response, self.correct_answers[0].answer_text)

    @patch.dict("os.environ", {"GEMINI_API_KEY": ""})
    def test_quiz_generation_returns_configuration_error_without_key(self):
        response = self.client.post(
            reverse("lms:generate_quiz", args=[self.lesson.pk]), follow=True
        )

        self.assertContains(response, "Quiz generation is not configured")

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"})
    @patch(
        "lms.views._request_gemini_quiz",
        new_callable=AsyncMock,
        side_effect=HTTPError(None, 429, "Too Many Requests", None, None),
    )
    def test_quiz_generation_shows_quota_error(self, _generate_quiz):
        response = self.client.post(
            reverse("lms:generate_quiz", args=[self.lesson.pk]), follow=True
        )

        self.assertContains(response, "quota or rate limit was reached")

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"})
    @patch(
        "lms.views._request_gemini_quiz",
        new_callable=AsyncMock,
        side_effect=HTTPError(None, 503, "Service Unavailable", None, None),
    )
    def test_quiz_generation_shows_retry_message_for_service_unavailable(
        self, _generate_quiz
    ):
        response = self.client.post(
            reverse("lms:generate_quiz", args=[self.lesson.pk]), follow=True
        )

        self.assertContains(response, "Gemini is temporarily unavailable (HTTP 503)")
        self.assertContains(response, "try generating the quiz again")

    def test_course_detail_returns_course_page(self):
        response = self.client.get(
            reverse("lms:course_detail", args=[self.course.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.course.title)

    def test_lesson_detail_embeds_video_and_exposes_quiz_control(self):
        response = self.client.get(
            reverse("lms:lesson_detail", args=[self.lesson.pk])
        )

        self.assertContains(response, "<video", html=False)
        self.assertContains(response, self.lesson.video_url)
        self.assertContains(response, 'id="generate-quiz"', html=False)

    def test_external_video_page_is_linked_instead_of_embedded_as_a_video(self):
        self.lesson.video_url = (
            "https://www.istockphoto.com/video/example-video-123"
        )
        self.lesson.save(update_fields=["video_url"])

        response = self.client.get(
            reverse("lms:lesson_detail", args=[self.lesson.pk])
        )

        self.assertNotContains(response, "<video", html=False)
        self.assertContains(response, "Open video source")
        self.assertContains(response, self.lesson.video_url)

    def test_signed_out_learner_gets_sign_in_link_for_quiz(self):
        self.client.logout()

        response = self.client.get(
            reverse("lms:lesson_detail", args=[self.lesson.pk])
        )

        self.assertContains(response, "Sign in to generate a quiz")
        self.assertContains(
            response,
            f'{reverse("login")}?next={reverse("lms:lesson_detail", args=[self.lesson.pk])}',
        )

    def test_course_catalog_is_public_and_links_to_course_detail(self):
        self.client.logout()

        response = self.client.get(reverse("lms:course_catalog"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.course.title)
        self.assertContains(
            response, reverse("lms:course_detail", args=[self.course.pk])
        )

    def test_course_and_lesson_creation_are_staff_only(self):
        response = self.client.get(reverse("lms:course_upload"))
        self.assertEqual(response.status_code, 302)

        response = self.client.get(
            reverse("lms:video_lesson_upload", args=[self.course.pk])
        )
        self.assertEqual(response.status_code, 302)

        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        response = self.client.get(reverse("lms:course_upload"))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(
            reverse("lms:video_lesson_upload", args=[self.course.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_admin_navigation_link_is_visible_only_to_staff_users(self):
        response = self.client.get(
            reverse("lms:course_detail", args=[self.course.pk])
        )
        self.assertNotContains(response, reverse("admin:index"))

        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        response = self.client.get(
            reverse("lms:course_detail", args=[self.course.pk])
        )
        self.assertContains(response, reverse("admin:index"))

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"})
    @patch(
        "lms.views._request_gemini_recommendations",
        new_callable=AsyncMock,
    )
    def test_recommendations_use_activity_and_link_only_existing_available_courses(
        self, generate_recommendations
    ):
        available_course = Course.objects.create(
            title="Advanced Python",
            description="Explore advanced Python concepts.",
        )
        CourseCompletion.objects.create(
            user=self.user,
            course=self.course,
            is_completed=True,
            completed_at=timezone.now(),
        )
        QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz,
            score=50,
            correct_answers=1,
            total_questions=2,
            answer_results=[],
        )
        generate_recommendations.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '[{"title":"Advanced Python",'
                                    '"description":"Build on what you learned.",'
                                    '"relevance_score":0.9},'
                                    '{"title":"Nonexistent Course",'
                                    '"description":"Not in the LMS.",'
                                    '"relevance_score":0.8}]'
                                )
                            }
                        ]
                    }
                }
            ]
        }

        response = self.client.get(reverse("lms:recommend_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recommended for You")
        self.assertContains(response, available_course.title)
        self.assertNotContains(response, "Nonexistent Course")
        self.assertNotContains(response, self.course.title)
        self.assertContains(
            response,
            reverse("lms:course_detail", args=[available_course.pk]),
        )
        activity = generate_recommendations.await_args.args[0]
        self.assertEqual(
            activity["completed_courses"][0]["title"], self.course.title
        )
        self.assertEqual(activity["recent_quiz_results"][0]["score"], 50)

    @patch.dict("os.environ", {"GEMINI_API_KEY": ""})
    def test_recommendation_dashboard_reports_missing_api_key(self):
        response = self.client.get(reverse("lms:recommend_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "require GEMINI_API_KEY")

    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"})
    @patch(
        "lms.views._request_gemini_recommendations",
        new_callable=AsyncMock,
    )
    def test_completed_courses_older_than_activity_window_are_not_recommended(
        self, generate_recommendations
    ):
        now = timezone.now()
        CourseCompletion.objects.create(
            user=self.user,
            course=self.course,
            is_completed=True,
            completed_at=now - timedelta(days=20),
        )
        for day in range(1, 11):
            completed_course = Course.objects.create(
                title=f"Completed course {day}",
                description="Already completed.",
            )
            CourseCompletion.objects.create(
                user=self.user,
                course=completed_course,
                is_completed=True,
                completed_at=now - timedelta(days=day),
            )
        available_course = Course.objects.create(
            title="Available course",
            description="Not completed.",
        )
        generate_recommendations.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '[{"title":"Python basics",'
                                    '"description":"No longer available.",'
                                    '"relevance_score":0.9},'
                                    '{"title":"Available course",'
                                    '"description":"Ready to learn.",'
                                    '"relevance_score":0.8}]'
                                )
                            }
                        ]
                    }
                }
            ]
        }

        response = self.client.get(reverse("lms:recommend_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, available_course.title)
        catalog = generate_recommendations.await_args.args[1]
        self.assertNotIn(self.course.title, [course["title"] for course in catalog])
        self.assertNotIn(
            "Completed course 1", [course["title"] for course in catalog]
        )

    def test_recommendation_dashboard_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("lms:recommend_courses"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('lms:recommend_courses')}",
        )


class ModelFormTests(TestCase):
    def test_course_form_accepts_valid_data(self):
        form = CourseForm(
            data={"title": "Django", "description": "Build web applications."}
        )

        self.assertTrue(form.is_valid())

    def test_course_form_rejects_missing_required_data(self):
        form = CourseForm(data={"title": "", "description": ""})

        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)
        self.assertIn("description", form.errors)

    def test_video_lesson_form_accepts_valid_data(self):
        form = VideoLessonForm(
            data={
                "title": "Routing",
                "video_url": "https://example.com/routing",
                "description": "Learn URL routing.",
            }
        )

        self.assertTrue(form.is_valid())

    def test_video_lesson_form_rejects_invalid_url(self):
        form = VideoLessonForm(
            data={
                "title": "Routing",
                "video_url": "not-a-url",
                "description": "Learn URL routing.",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("video_url", form.errors)


class ModelLifecycleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="model-user")
        self.course = Course.objects.create(
            title="Course", description="Course description."
        )
        self.lesson = VideoLesson.objects.create(
            course=self.course,
            title="Lesson",
            video_url="https://example.com/lesson",
            description="Lesson description.",
        )
        self.quiz = Quiz.objects.create(
            course=self.course, lesson=self.lesson, title="Quiz"
        )
        self.question = Question.objects.create(
            quiz=self.quiz, question_text="Question?"
        )
        self.answer = Answer.objects.create(
            question=self.question, answer_text="Answer", is_correct=True
        )

    def test_course_can_be_created_updated_and_deleted(self):
        self.course.title = "Updated Course"
        self.course.save()
        self.assertEqual(Course.objects.get(pk=self.course.pk).title, "Updated Course")

        course_id = self.course.pk
        self.course.delete()
        self.assertFalse(Course.objects.filter(pk=course_id).exists())

    def test_video_lesson_can_be_created_updated_and_deleted(self):
        self.lesson.title = "Updated Lesson"
        self.lesson.save()
        self.assertEqual(
            VideoLesson.objects.get(pk=self.lesson.pk).title, "Updated Lesson"
        )

        lesson_id = self.lesson.pk
        self.lesson.delete()
        self.assertFalse(VideoLesson.objects.filter(pk=lesson_id).exists())

    def test_quiz_can_be_created_updated_and_deleted(self):
        self.quiz.title = "Updated Quiz"
        self.quiz.save()
        self.assertEqual(Quiz.objects.get(pk=self.quiz.pk).title, "Updated Quiz")

        quiz_id = self.quiz.pk
        self.quiz.delete()
        self.assertFalse(Quiz.objects.filter(pk=quiz_id).exists())

    def test_question_can_be_created_updated_and_deleted(self):
        self.question.question_text = "Updated question?"
        self.question.save()
        self.assertEqual(
            Question.objects.get(pk=self.question.pk).question_text,
            "Updated question?",
        )

        question_id = self.question.pk
        self.question.delete()
        self.assertFalse(Question.objects.filter(pk=question_id).exists())

    def test_answer_can_be_created_updated_and_deleted(self):
        self.answer.answer_text = "Updated answer"
        self.answer.is_correct = False
        self.answer.save()
        saved_answer = Answer.objects.get(pk=self.answer.pk)
        self.assertEqual(saved_answer.answer_text, "Updated answer")
        self.assertFalse(saved_answer.is_correct)

        answer_id = self.answer.pk
        self.answer.delete()
        self.assertFalse(Answer.objects.filter(pk=answer_id).exists())

    def test_quiz_attempt_can_be_created_updated_and_deleted(self):
        attempt = QuizAttempt.objects.create(
            user=self.user,
            quiz=self.quiz,
            score=50,
            correct_answers=1,
            total_questions=2,
        )
        attempt.score = 100
        attempt.save()
        self.assertEqual(QuizAttempt.objects.get(pk=attempt.pk).score, 100)

        attempt_id = attempt.pk
        attempt.delete()
        self.assertFalse(QuizAttempt.objects.filter(pk=attempt_id).exists())

    def test_course_completion_can_be_created_updated_and_deleted(self):
        completion = CourseCompletion.objects.create(
            user=self.user, course=self.course
        )
        completion.is_completed = True
        completion.completed_at = timezone.now()
        completion.save()
        self.assertTrue(
            CourseCompletion.objects.get(pk=completion.pk).is_completed
        )

        completion_id = completion.pk
        completion.delete()
        self.assertFalse(CourseCompletion.objects.filter(pk=completion_id).exists())


class RecommendationApiTests(TestCase):
    @patch("lms.views.urlopen")
    def test_recommendation_request_includes_grounding_schema_and_user_context(
        self, mocked_urlopen
    ):
        mocked_urlopen.return_value.__enter__.return_value.read.return_value = (
            b'{"candidates":[{"content":{"parts":[{"text":"[]"}]}}]}'
        )
        activity = {
            "completed_courses": [{"title": "Python"}],
            "recent_quiz_results": [{"score": 75}],
        }
        catalog = [{"title": "Django", "description": "Web development."}]

        from .views import _request_gemini_recommendations

        asyncio.run(
            _request_gemini_recommendations(activity, catalog, "test-api-key")
        )

        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertIn(
            "/models/gemini-3.6-flash:generateContent", request.full_url
        )
        self.assertEqual(payload["tools"], [{"google_search": {}}])
        self.assertEqual(
            payload["generationConfig"]["responseSchema"]["items"]["required"],
            ["title", "description", "relevance_score"],
        )
        prompt = payload["contents"][0]["parts"][0]["text"]
        self.assertIn('"title": "Python"', prompt)
        self.assertIn('"score": 75', prompt)
        self.assertIn('"title": "Django"', prompt)

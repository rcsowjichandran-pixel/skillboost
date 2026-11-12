from django.urls import path

from . import views

app_name = "lms"

urlpatterns = [
    path("", views.course_catalog, name="course_catalog"),
    path("discover/", views.recommend_courses, name="recommend_courses"),
    path("accounts/signup/", views.learner_signup, name="learner_signup"),
    path("accounts/profile/", views.learner_profile, name="learner_profile"),
    path("courses/upload/", views.course_upload, name="course_upload"),
    path("courses/<int:course_id>/", views.course_detail, name="course_detail"),
    path(
        "courses/<int:course_id>/certificate/<int:user_id>/",
        views.generate_certificate,
        name="generate_certificate",
    ),
    path(
        "courses/<int:course_id>/lessons/upload/",
        views.video_lesson_upload,
        name="video_lesson_upload",
    ),
    path("lessons/<int:lesson_id>/", views.lesson_detail, name="lesson_detail"),
    path(
        "lessons/<int:lesson_id>/quiz/generate/",
        views.generate_quiz,
        name="generate_quiz",
    ),
    path("quizzes/<int:quiz_id>/", views.quiz_take, name="quiz_take"),
    path(
        "quizzes/<int:quiz_id>/submit/",
        views.quiz_submit,
        name="quiz_submit",
    ),
    path(
        "quiz-attempts/<int:attempt_id>/results/",
        views.quiz_results,
        name="quiz_results",
    ),
]

from django.contrib import admin

from .models import (
    Answer,
    Course,
    CourseCompletion,
    Question,
    Quiz,
    QuizAttempt,
    VideoLesson,
)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("title", "created_at")
    search_fields = ("title",)


@admin.register(VideoLesson)
class VideoLessonAdmin(admin.ModelAdmin):
    list_display = ("title", "course")
    list_filter = ("course",)
    search_fields = ("title", "course__title")


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "lesson")
    list_filter = ("course",)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("question_text", "quiz")
    search_fields = ("question_text",)


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ("answer_text", "question", "is_correct")
    list_filter = ("is_correct",)


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ("user", "quiz", "score", "created_at")
    list_filter = ("quiz",)
    search_fields = ("user__username", "quiz__title")


@admin.register(CourseCompletion)
class CourseCompletionAdmin(admin.ModelAdmin):
    list_display = ("user", "course", "is_completed", "completed_at")
    list_filter = ("is_completed", "course")
    search_fields = ("user__username", "course__title")

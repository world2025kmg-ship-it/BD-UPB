from fastapi import FastAPI

from . import admin, attendance, journal, partner, session, students


def register_routers(app: FastAPI) -> None:
    app.include_router(session.router)
    app.include_router(students.router)
    app.include_router(partner.router)
    app.include_router(admin.router)
    app.include_router(journal.router)
    app.include_router(attendance.router)

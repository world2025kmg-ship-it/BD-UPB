"""Modèles utilisés UNIQUEMENT pour documenter l'API dans OpenAPI/Swagger.

Les routes lisent le JSON à la main (pour conserver les messages d'erreur
d'origine) : ces modèles n'ont aucun effet à l'exécution, ils alimentent
`/docs`, `/redoc` et `/openapi.json`.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MATRICULE_EXAMPLE = "KOUA1703980001"


# --------------------------------------------------------------------------- erreurs
class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    message: str = Field(examples=["Matricule requis."])


class ConflictEnrollResponse(ErrorResponse):
    numero_candidat: str = Field(examples=["GENIE-2026-A1B2C"])


class ImportErrorResponse(ErrorResponse):
    errors: list[str] | None = Field(
        default=None,
        description="50 erreurs au maximum, une par ligne fautive.",
        examples=[["Ligne 3 : adresse e-mail invalide."]],
    )


def errors(*codes: int) -> dict[int, dict[str, Any]]:
    """Bloc `responses=` listant les erreurs possibles d'une route."""
    descriptions = {
        400: "Requête invalide (champ manquant, date mal formée…).",
        401: "Jeton administrateur absent ou invalide.",
        404: "Ressource introuvable.",
        409: "Conflit (déjà existant).",
        422: "Validation métier échouée.",
        500: "Erreur serveur ou base de données.",
        503: "Jeton administrateur non configuré côté serveur.",
    }
    return {c: {"model": ErrorResponse, "description": descriptions[c]} for c in codes}


# --------------------------------------------------------------------------- entrées
def request_body(model: type[BaseModel], description: str) -> dict[str, Any]:
    """`openapi_extra` décrivant un corps JSON lu manuellement."""
    return {
        "requestBody": {
            "required": True,
            "description": description,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }


class MatriculeBody(BaseModel):
    matricule: str = Field(
        max_length=40,
        description="Matricule de l'étudiant (insensible à la casse, espaces ignorés).",
        examples=[MATRICULE_EXAMPLE],
    )


class ModificationBody(BaseModel):
    """Au moins un champ à corriger en plus du matricule."""

    matricule: str = Field(examples=[MATRICULE_EXAMPLE])
    nom: str | None = Field(default=None, max_length=150)
    prenoms: str | None = Field(default=None, max_length=150)
    date_naissance: str | None = Field(
        default=None,
        description="`AAAA-MM-JJ`, `JJ/MM/AAAA` ou `JJ-MM-AAAA` ; pas de date future.",
        examples=["1998-03-17"],
    )
    filiere: str | None = Field(default=None, max_length=150, description="Vide = effacer.")
    niveau: str | None = Field(default=None, max_length=150, description="Vide = effacer.")
    telephone: str | None = Field(default=None, max_length=40, description="Vide = effacer.")
    email: str | None = Field(default=None, examples=["nom@exemple.ci"], description="Vide = effacer.")
    sexe: str | None = Field(default=None, description="`M` ou `F` ; vide = effacer.")
    allergies_affection: str | None = Field(default=None, max_length=2000)


class UpdateStudentBody(BaseModel):
    id: int = Field(ge=1, description="Identifiant interne (champ `id` de `get_etudiants`).")
    telephone: str | None = None
    email: str | None = None
    sexe: str | None = Field(default=None, description="`M` ou `F`.")
    allergies_affection: str | None = Field(default=None, max_length=2000)
    date_naissance: str | None = Field(
        default=None, description="Recalcule le matricule ; `409` si le nouveau existe déjà."
    )


class UpdatePhoneBody(BaseModel):
    id: int = Field(ge=1)
    telephone: str


class ProcessModificationBody(BaseModel):
    id: int = Field(ge=1, description="Identifiant de la demande.")
    action: Literal["approve", "reject"]


class AttendanceBody(BaseModel):
    matricule: str = Field(examples=[MATRICULE_EXAMPLE])
    date: str | None = Field(default=None, description="`AAAA-MM-JJ` ; aujourd'hui par défaut.", examples=["2026-06-10"])
    jour_libelle: str | None = Field(default=None, examples=["Jour 1"])


# --------------------------------------------------------------------------- sorties
class SuccessResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str


class StudentRecord(BaseModel):
    matricule: str = Field(examples=[MATRICULE_EXAMPLE])
    nom: str = Field(examples=["Kouadio"])
    prenoms: str = Field(examples=["Alain"])
    date_naissance: str | None = Field(description="`AAAA-MM-JJ`", examples=["1998-03-17"])
    telephone: str | None = None
    email: str | None = None
    sexe: Literal["M", "F"] | None = None
    allergies_affection: str | None = None
    filiere: str | None = None
    niveau: str | None = None


class VerifyResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str = "Vos informations ont été retrouvées."
    data: StudentRecord


class EnrollData(BaseModel):
    matricule: str
    numero_candidat: str = Field(examples=["GENIE-2026-A1B2C"])
    statut: str = Field(examples=["EN_ATTENTE"])


class EnrollResponse(SuccessResponse):
    data: EnrollData


class AdminStudent(StudentRecord):
    id: int
    numero_candidat: str | None = Field(default=None, description="`null` si non inscrit.")
    statut: str | None = None


class LabelCount(BaseModel):
    libelle: str
    total: int


class StudentStats(BaseModel):
    total: int
    inscrits: int
    telephones: int
    emails: int
    filieres: list[LabelCount]
    niveaux: list[LabelCount]


class StudentsListResponse(BaseModel):
    status: Literal["success"] = "success"
    data: list[AdminStudent]
    total: int = Field(description="Nombre de résultats pour la recherche en cours.")
    page: int
    per_page: int
    total_pages: int
    stats: StudentStats = Field(description="Statistiques globales (indépendantes de la recherche).")


class PendingModification(BaseModel):
    id: int
    etudiant_id: int
    modifications: dict[str, Any] = Field(examples=[{"telephone": "0701020304"}])
    created_at: str = Field(examples=["2026-06-01 09:30:00"])
    matricule: str
    nom: str
    prenoms: str
    date_naissance: str | None


class ModificationsListResponse(BaseModel):
    status: Literal["success"] = "success"
    data: list[PendingModification]


class ModificationDecisionResponse(SuccessResponse):
    matricule: str | None = Field(default=None, description="Présent uniquement après approbation.")


class ImportResponse(SuccessResponse):
    imported: int
    matricules: list[str]


class AttendanceRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    etudiant_id: int
    matricule: str
    nom: str
    prenoms: str
    filiere: str | None
    niveau: str | None
    numero_candidat: str | None
    jour_libelle: str | None
    heure_pointage: str = Field(examples=["2026-06-10 08:15:30"])
    statut: str = Field(examples=["PRESENT"])
    qr_code_valide: bool
    created_at: str


class AttendanceStats(BaseModel):
    total_presents: int
    total_inscrits: int
    taux_presence: str = Field(examples=["42.5 %"])


class AttendanceReportResponse(BaseModel):
    status: Literal["success"] = "success"
    date: str
    jour: str | None
    stats: AttendanceStats
    data: list[AttendanceRow]


class AttendanceRecorded(BaseModel):
    id: int
    etudiant_id: int
    matricule: str
    date_pointage: str
    statut: Literal["PRESENT"]
    heure_pointage: str


class AttendanceRecordedResponse(SuccessResponse):
    data: AttendanceRecorded


class CreationBody(BaseModel):
    """Dossier d'un candidat dont le matricule est inconnu."""

    matricule: str = Field(description="Matricule saisi par le candidat.", examples=[MATRICULE_EXAMPLE])
    nom: str = Field(max_length=150, examples=["Kouadio"])
    prenoms: str = Field(max_length=150, examples=["Alain"])
    date_naissance: str = Field(description="`AAAA-MM-JJ`, `JJ/MM/AAAA` ou `JJ-MM-AAAA`.", examples=["1998-03-17"])
    filiere: str | None = Field(default=None, max_length=150)
    niveau: str | None = Field(default=None, max_length=150)
    telephone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, examples=["nom@exemple.ci"])
    sexe: str | None = Field(default=None, description="`M` ou `F`.")
    allergies_affection: str | None = Field(default=None, max_length=2000)


class PendingCreation(BaseModel):
    id: int
    matricule_saisi: str
    donnees: dict[str, Any]
    created_at: str = Field(examples=["2026-06-01 09:30:00"])


class CreationsListResponse(BaseModel):
    status: Literal["success"] = "success"
    data: list[PendingCreation]


class CreationDecisionResponse(SuccessResponse):
    matricule: str | None = Field(default=None, description="Matricule du dossier créé (après approbation).")


class LoginBody(BaseModel):
    identifiant: str = Field(examples=["admin"])
    password: str = Field(description="Mot de passe de l'administrateur.")


class LoginResponse(SuccessResponse):
    token: str = Field(description="Jeton de session : `Authorization: Bearer <token>`.")
    expires_in: int = Field(description="Durée de validité en secondes.", examples=[28800])
    identifiant: str


class PartnerVerifyBody(BaseModel):
    matricule: str = Field(max_length=40, examples=[MATRICULE_EXAMPLE])
    numero_candidat: str | None = Field(
        default=None,
        max_length=40,
        description="Numéro de candidat attribué par le site d'inscription ; enregistré à la "
        "première inscription (un numéro est généré s'il est absent).",
    )


class PartnerRegistration(BaseModel):
    numero_candidat: str = Field(examples=["GENIE-2026-A1B2C"])
    statut: str = Field(examples=["EN_ATTENTE"])
    deja_inscrit: bool = Field(description="`true` si l'étudiant était déjà inscrit avant cet appel.")


class PartnerVerifyResponse(SuccessResponse):
    data: StudentRecord
    inscription: PartnerRegistration


class ApproveFailure(BaseModel):
    id: int
    message: str = Field(examples=["Le matricule calculé existe déjà pour un autre étudiant."])


class ApproveBatch(BaseModel):
    approuvees: int
    echecs: list[ApproveFailure]


class ApproveAllResponse(SuccessResponse):
    modifications: ApproveBatch = Field(description="Demandes de correction.")
    creations: ApproveBatch = Field(description="Demandes d'ajout de dossier.")


class JournalEntry(BaseModel):
    id: int
    created_at: str = Field(examples=["2026-11-08 09:30:12"])
    acteur_type: Literal["ADMIN", "PARTENAIRE", "PUBLIC", "DOCUMENTATION", "SYSTEME"]
    acteur: str | None = Field(description="Identifiant admin, nom du partenaire… (`null` si anonyme).")
    categorie: str = Field(examples=["AUTH"])
    action: str = Field(examples=["ADMIN_CONNEXION"])
    statut: Literal["SUCCES", "ECHEC"]
    cible_type: str | None = Field(examples=["matricule"])
    cible: str | None = Field(examples=["KOUA1703980001"])
    message: str | None = Field(description="Motif en cas d'échec.")
    details: dict[str, Any] | None = Field(description="Informations complémentaires (noms de champs, compteurs…).")
    code_http: int | None
    ip: str | None
    methode: str | None
    chemin: str | None
    user_agent: str | None


class JournalStats(BaseModel):
    total: int = Field(description="Nombre total d'événements enregistrés.")
    dernieres_24h: int
    echecs_24h: int
    acces_refuses_24h: int = Field(description="Connexions échouées et accès refusés des dernières 24 h.")


class JournalResponse(BaseModel):
    status: Literal["success"] = "success"
    data: list[JournalEntry]
    total: int = Field(description="Nombre d'événements correspondant aux filtres.")
    page: int
    per_page: int
    total_pages: int
    stats: JournalStats
    actions: list[str] = Field(description="Codes d'action connus (pour construire un filtre).")
    categories: list[str]


class ExportLogBody(BaseModel):
    format: Literal["excel", "pdf"]
    cible: Literal["etudiants", "pointage"]
    lignes: int | None = Field(default=None, ge=0, description="Nombre de lignes exportées.")

"""Playwright observer that launches and drives the account's own browser.

The observer launches the Chromium-family binary with a persistent user-data
dir (the saved account profile), navigates it to Google sign-in, and drives the
flow:

- reads the current page state (URL, title, visible text) and classifies it with
  login_policy.classify_page;
- advances *only* ordinary consent interstitials (I agree / Continue pages on
  accounts.* / consent.*) by clicking the match;
- when BROWSER_AUTO_LOGIN is enabled and the account has a stored encrypted
  secret, types the identifier and password into Google's own sign-in form
  (accounts.google.com / consent.google.com only) and submits each step;
- when a 2FA secret is stored, generates the TOTP and enters it;
- reports CAPTCHA / MFA / security / unknown pages by calling
  observe_session_page, which pauses the session for a human;
- keeps watching after a page pauses, so the mailbox is detected as soon as the
  human clears a challenge and Gmail loads.

Launching via Playwright's persistent context keeps Chrome's own command line
free of --remote-debugging-port / --enable-automation, which is what makes
Google answer "this browser or app may not be secure" instead of loading the
form. The saved profile (cookies/state) stays on disk in the profile dir and is
reused by later sessions, so an account that has signed in once can be opened
again without re-authentication.

It can hand supported widget tokens to 2Captcha when enabled, but still waits
for manual completion of unsupported challenges. It only types into
Google's own login host, never touches already-filled fields, and attempts each
page once so a rejected password cannot be retried in a loop. Every fill and
submit is recorded as an audit event. No fingerprint/stealth signal beyond
disabling Chrome's own automation infobar is manipulated and cookies are never
cleared.
"""

from __future__ import annotations

import logging
import hashlib
import os
import random
import re
import threading
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urljoin, urlparse

from .config import get_settings
from .captcha_solver import apply_token, detect_widget, response_present, solve_widget
from .login_policy import PageAction, PageObservation, PageSignal, classify_page, is_totp_challenge_url
from .newsletter_agent import request_newsletter_action_plan, request_newsletter_control_suggestion
from .newsletter_execution import AGENT_LEARNING, DETERMINISTIC, LIVE_FORM, MANUAL, SKILL_REPLAY, allows_newsletter_agent
from .newsletter_fields import LIVE_FILL_KEYS, agreement_checkbox_key, attribute_field_hints, blocked_live_control_reason, preferred_option_index, semantic_field_key
from .newsletter_identity import GENERATED_FIELD_KEYS
from .runtime import resolve_browser_executable
from .security import generate_totp

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

_SAFE_CONSENT_LABELS = ("i agree", "accept all", "continue", "continuer", "continuar", "next", "got it", "ok", "i understand", "understand")
_SUBMIT_LABELS = ("sign in", "log in", "continue", "next")
_LOGIN_HOSTS = {"accounts.google.com", "consent.google.com"}
_ACCOUNT_HANDOFF_HOSTS = _LOGIN_HOSTS | {"mail.google.com", "myaccount.google.com"}
_BLANK_URL_PREFIXES = ("about:", "chrome:", "devtools:")
_BLANK_PAGE_RETRY_LIMIT = 3
_BLANK_CONTENT_TIMEOUT_SECONDS = 25.0
_BROWSER_LAUNCH_RETRY_LIMIT = 3
_BROWSER_LAUNCH_RETRY_DELAY_SECONDS = 1.0
_OPERATION_FAILURE_EVENTS = {
    "login": "login.failed",
    "reuse": "reuse.failed",
    "contacts": "contacts.failed",
    "labels_markers": "labels_markers.failed",
    "security": "security.failed",
    "addons": "addons.failed",
    "mail": "mail.failed",
    "alerts": "alerts.failed",
    "credential_change": "credential_change.failed",
    "newsletter": "newsletter.signup.failed",
    "newsletter_confirm": "newsletter.confirmation.failed",
    "account_preferences": "account_preferences.failed",
}
_CREDENTIAL_CHANGE_URL = "https://myaccount.google.com/signinoptions/password"
_CREDENTIAL_CHANGE_PAGE_PATH = "/signinoptions/password"
_TWO_STEP_URL = "https://myaccount.google.com/two-step-verification/authenticator"
_TWO_STEP_PAGE_PATHS = ("/two-step-verification", "/signinoptions/two-step-verification", "/signinoptions/twosv")
_RECOVERY_URL = "https://myaccount.google.com/recovery/email"
_RECOVERY_PAGE_PATHS = ("/recovery/email", "/recovery")
_PASSWORD_CHANGE_SUBMIT_LABELS = ("change password", "update password", "save password", "changer le mot de passe", "modifier le mot de passe", "mettre à jour le mot de passe", "mettre a jour le mot de passe", "cambiar contraseña", "cambiar la contraseña", "cambiar contrasena", "actualizar contraseña", "actualizar contrasena", "apply", "accepter", "aceptar")
_MYACCOUNT_PASSWORD_ERROR_TERMS = ("something went wrong", "couldn't change", "could not change", "unable to change", "unable to update", "wrong password", "your password may not be correct", "an error occurred")
_MYACCOUNT_PASSWORD_SUCCESS_TERMS = (
    "password changed",
    "password updated",
    "password saved",
    "new password saved",
    "password has been changed",
    "password has been updated",
    "password was changed",
    "password was updated",
    "password change was successful",
    "password changed successfully",
    "password successfully changed",
    "password was changed successfully",
    "password updated successfully",
    "password successfully updated",
    "you changed your password",
    "you've changed your password",
    "you have changed your password",
    "your password has been changed",
    "your password has been updated",
    "your password was changed",
    "your password was updated",
    "your password is updated",
    "contrasena actualizada",
    "contrasena actualizado",
    "contrasena cambiada",
    "contrasena cambiado",
    "contrasena modificada",
    "se ha cambiado la contrasena",
    "se cambio la contrasena",
    "la contrasena se ha cambiado",
    "tu contrasena ha sido cambiada",
    "contrasena actualizada correctamente",
    "mot de passe modifie",
    "mot de passe change",
    "mot de passe a ete change",
    "le mot de passe a ete modifie",
    "mot de passe mis a jour",
    "passwort geandert",
    "passwort wurde geandert",
    "passwort erfolgreich geandert",
    "passwort aktualisiert",
    "senha alterada",
    "senha foi alterada",
    "senha atualizada",
    "senha alterada com sucesso",
    "password modificata",
    "password aggiornata",
    "password cambiata",
    "wachtwoord gewijzigd",
    "wachtwoord is gewijzigd",
    "wachtwoord veranderd",
    "heslo bylo zmeneno",
    "haslo zostalo zmienione",
    "lozinka promijenjena",
    "parola degistirildi",
    "parola guncellendi",
    "пароль изменен",
    "пароль был изменен",
    "пароль успешно изменен",
    "パスワードを変更しました",
    "パスワードを更新しました",
    "パスワードが変更されました",
    "비밀번호가 변경되었습니다",
    "비밀번호가 변경됨",
    "비밀번호 변경 완료",
    "密码已更改",
    "密码已修改",
    "密码更改成功",
    "密碼已變更",
    "密碼已修改",
)
_PASSWORD_CHANGE_SUCCESS_JS_RE = (
    "((?:^|[^a-z])("
    + "|".join(re.escape(term).replace(r"\ ", r"[^]{0,12}") for term in _MYACCOUNT_PASSWORD_SUCCESS_TERMS)
    + ")(?:[^a-z]|$))"
    + "|((password|contrasena|contraseña|passwort|senha|wachtwoord|mot\\s*de\\s*passe|parol[ae]|haslo|heslo|lozinka)\\b[^]{0,60}(cambiad|actualizad|modificad|changed|updated|geandert|aktualisiert|alterada|atualizada|aggiornata|modificata|gewijzigd|veranderd|zmienione|zmeneno|promijenjena|degistirildi|guncellendi))"
    + "|((cambiad|actualizad|modificad|changed|updated|geandert|aktualisiert|alterada|atualizada|aggiornata|modificata|gewijzigd|veranderd|zmienione|zmeneno|promijenjena|degistirildi|guncellendi)[^]{0,60}(password|contrasena|contraseña|passwort|senha|wachtwoord|mot\\s*de\\s*passe|parol[ae]|haslo|heslo|lozinka))"
    + "|((mot\\s*de\\s*passe)[^]{0,60}(modif|chang))"
    + "|((modif|chang)[^]{0,60}(mot\\s*de\\s*passe))"
    + "|((пароль)[^]{0,60}(изменен|сменен|обновлен|успешно))"
    + "|((изменен|сменен|обновлен)[^]{0,60}(пароль))"
    + "|((パスワード)[^]{0,20}(変更|更新))"
    + "|((变更|更新)[^]{0,20}(密码|密碼))"
    + "|((密码|密碼)[^]{0,20}(更改|变更|修改|已改))"
    + "|((비밀번호)[^]{0,20}(변경|변경됨|완료))"
)
_TWO_STEP_GET_STARTED_LABELS = ("get started", "turn on 2-step verification", "turn on two-step verification", "add authenticator", "add authenticator app", "add a second step", "set up authenticator", "set up authenticator app", "activate 2-step verification", "activate two-step verification", "begin setup", "set up", "start", "comenzar", "empezar", "iniciar", "activar", "configurar", "activar la verificacion en dos pasos", "activar la verificacion en 2 pasos", "activar la validacion en dos pasos", "anadir autenticador", "agregar autenticador", "agregar una aplicacion de autenticador", "configurar autenticador", "configurar la aplicacion de autenticador", "commencer", "activer", "configurer", "activer la validation en deux etapes", "activer la verification en deux etapes", "ajouter un authentificateur", "ajouter une application d'authentification", "configurer l'application d'authentification", "commencer la configuration")
_TWO_STEP_MANUAL_LABELS = ("can't scan it", "cant scan it", "cannot scan it", "can't scan the code", "cannot scan the code", "can't scan the qr code", "cannot scan the qr code", "i can't scan it", "i cant scan it", "can't scan", "can't it be scanned", "cant it be scanned", "cannot it be scanned", "can it not be scanned", "trouble scanning", "having trouble scanning", "enter a setup key", "enter the setup key", "enter this key", "setup key", "secret key", "manual entry", "use a setup key", "get a verification code", "no puedes escanear", "no puedes escanearlo", "no puedo escanear", "no puedo escanearlo", "no se puede escanear", "no se puede escanear el codigo", "no puedes escanear el codigo", "no puedes escanear el codigo qr", "no puedes leerlo", "ingresar clave", "ingresar la clave", "introducir clave", "introducir la clave", "clave de configuracion", "clave secreta", "ne peut on pas le scanner", "ne peut-on pas le scanner", "saisir la clé", "saisir la cle", "saisir la cle de configuration", "entrer la clé", "entrer la cle", "entrer la cle de configuration", "cle de configuration", "cle secrete", "vous ne pouvez pas scanner", "je ne peux pas scanner", "impossible de scanner", "impossible de scanner le code", "impossible de scanner le code qr", "code kann nicht gescannt werden", "einrichtungsschluessel", "geheimschluessel")
_TWO_STEP_REMOVE_LABELS = ("remove authenticator", "remove authenticator app", "remove google authenticator", "remove microsoft authenticator", "delete authenticator", "remove app", "remove", "delete", "eliminar autenticador", "eliminar el autenticador", "eliminar el autenticador de google", "eliminar aplicacion", "eliminar la aplicacion de autenticador", "eliminar", "borrar autenticador", "borrar", "quitar el autenticador", "quitar el autenticador de google", "quitar autenticador", "quitar", "supprimer l'authentificateur", "supprimer l'authentificateur de google", "supprimer l'application", "supprimer l'application d'authentification", "supprimer", "retirer", "authentifikator entfernen", "entfernen", "rimuovi autenticatore", "rimuovi", "remover autenticador", "remover", "excluir", "verwijderen")
_TWO_STEP_ROW_EDIT_LABELS = ("edit", "edit authenticator", "change authenticator", "change authenticator app", "change", "more options", "more", "manage", "editar", "modificar", "cambiar la app de autenticador", "cambiar la aplicacion de autenticador", "cambiar la app", "cambiar la aplicacion", "cambiar", "gestionar", "modifier", "editer", "gerer", "changer", "changer l'application", "changer l'authentificateur", "modifier l'application")
_TWO_STEP_AUTH_NAME_TERMS = ("google authenticator", "microsoft authenticator", "authenticator app", "authenticator", "authenticator application", "authentifikator", "authentificateur", "authenticator-app", "autenticador", "autenticador de google", "autenticatore", "app de autenticador", "aplicacion de autenticador", "aplicacion de autenticacion", "application d'authentification", "application d'authentification google", "authentificateur de google")
_TWO_STEP_CONFIRM_REMOVE_LABELS = ("remove authenticator", "remove authenticator app", "remove app", "remove", "delete", "yes, remove", "yes, delete", "confirm", "quitar el autenticador de google", "eliminar el autenticador de google", "eliminar el autenticador", "eliminar autenticador", "eliminar", "si, quitar", "si, eliminar", "quitar el autenticador", "quitar autenticador", "quitar", "aceptar", "suprimir", "supprimer l'authentificateur", "supprimer l'application", "supprimer", "oui, supprimer", "confirmer", "confirmar")
_TWO_STEP_AUTHENTICATOR_NEXT_LABELS = ("next", "continue", "following", "siguiente", "continuar", "avanzar", "suivant", "continuer", "weiter", "weiteren")
_TWO_STEP_DEVICE_LABELS = ("android", "iphone", "ipad", "ios", "install", "instalar", "installer")
_TWO_STEP_CONFIRM_LABELS = ("turn on", "turn on 2-step verification", "done", "activate", "enable", "finish", "activar", "activar la verificacion en dos pasos", "habilitar", "finalizar", "listo", "terminar", "agregar", "añadir", "ajouter", "confirmar", "confirmer", "activer", "activer la validation en deux etapes", "terminer", "finaliser", "prêt", "fertig")
_TWO_STEP_DONE_TERMS = ("two-step verification is on", "2-step verification is on", "2-step verification is now on", "two-step verification turned on", "you've added an authenticator", "your authenticator app was added", "authenticator app added", "authenticator added", "authenticator added successfully", "authenticator setup complete", "authenticator has been added", "la verification en deux etapes est activee", "verification en deux etapes est activee", "la validation en deux etapes est activee", "validation en deux etapes activee", "authentificateur ajoute", "application d'authentification ajoutee", "l'authentificateur a ete ajoute", "authentificateur ajoute avec succes", "configuration terminee", "la verificacion en dos pasos esta activada", "verificacion en dos pasos activada", "se activo la verificacion en dos pasos", "se agrego el autenticador", "se agrego la aplicacion de autenticacion", "autenticador agregado", "autenticador configurado", "autenticador agregado correctamente", "se ha anadido el autenticador", "configuracion completada", "die bestatigung in zwei schritten ist aktiviert")
_RECOVERY_EDIT_LABELS = ("edit recovery email", "change recovery email", "edit", "change", "update", "modifier", "modifica", "cambiar", "editar")
_RECOVERY_SAVE_LABELS = ("save", "save recovery email", "update recovery email", "change recovery email", "update", "done", "confirm", "sauvegarder", "enregistrer", "guardar", "guardar cambios", "actualizar")
_RECOVERY_SUCCESS_TERMS = ("recovery email updated", "recovery email was updated", "recovery email changed", "recovery email has been updated", "recovery email saved", "your recovery email was updated", "recovery email is updated", "adresse de recuperation mise a jour", "adresse de récupération mise à jour", "correo de recuperacion actualizado", "correo de recuperación actualizado")
_RECOVERY_ERROR_TERMS = ("something went wrong", "couldn't update", "could not update", "unable to update", "unable to save", "an error occurred", "se produjo un error", "no se pudo actualizar", "no se pudo guardar")
_CREDENTIAL_FIELD_SELECTOR = "input[type=email]:visible, input[name=identifier]:visible, input[id=identifierId]:visible, input[type=password]:visible, input[name=Passwd]:visible, input#password:visible"
_OTP_FIELD_SELECTOR = "input[inputmode=numeric]:visible, input#totpPin:visible, input[name=totpPin]:visible, input[autocomplete=one-time-code]:visible, input[type=tel][autocomplete=one-time-code]:visible"
_OTP_MAX_RETRIES = 3
_ADDONS_POPUP_STALL_SECONDS = 45.0
_ADDONS_POPUP_MISSING_SECONDS = 6.0
_ADDONS_POPUP_CRASH_SECONDS = 3.0
_ADDONS_POPUP_ERROR_SECONDS = 2.0
_ADDONS_POPUP_RETRY_LIMIT = 2
_NEWSLETTER_CHALLENGE_WAIT_SECONDS = 10.0
_NEWSLETTER_POST_SUBMIT_SETTLE_SECONDS = 5.0
_NEWSLETTER_TASK_TIMEOUT_SECONDS = 180.0
NO_CHALLENGE = "NO_CHALLENGE"
SUSPICIOUS_CHALLENGE = "SUSPICIOUS"
CONFIRMED_CHALLENGE = "CONFIRMED_CHALLENGE"
# Only phrases an interstitial shows on its own page. A bare "turnstile" or
# "captcha" occurs on any page that merely embeds the widget -- or, on a site
# that writes about ad technology, simply discusses it -- so matching those
# reported a working signup form as a confirmed challenge. A rendered widget is
# still detected through its own iframe by ``_newsletter_dom_challenge_reason``.
_NEWSLETTER_CHALLENGE_MARKERS = (
    "cf-chl-", "challenge-platform", "just a moment",
    "checking your browser", "verify you are human",
)
# The caller reports this probe as ``visible_widget``, so the query has to mean
# visibility. Turnstile and reCAPTCHA v3 ship an *invisible* widget by default:
# the iframe is in the markup with no box, never gates the form, and the page
# signs a visitor up normally. Counting matching iframes by presence alone
# therefore reported a page whose signup form was plainly rendered as a
# confirmed challenge, and every attempt ended as BLOCKED_ANTIBOT after the
# bounded wait. A real interstitial still matches, because it renders its
# widget -- and it also carries one of the copy markers above.
_NEWSLETTER_CHALLENGE_IFRAME_SELECTOR = (
    'iframe[src*="turnstile"]:visible, '
    'iframe[src*="challenge"]:visible, '
    'iframe[src*="captcha"]:visible'
)
_OTP_REJECTION_TERMS = (
    "wrong code",
    "incorrect code",
    "invalid code",
    "code has expired",
    "code expired",
    "try again",
    "code incorrect",
    "code invalide",
    "code expire",
    "réessayez",
    "ressayez",
    "codigo incorrecto",
    "código incorrecto",
    "codigo no valido",
    "código no válido",
    "codigo expirado",
    "código expirado",
    "intentalo de nuevo",
    "inténtalo de nuevo",
)


class BrowserObserver:
    def __init__(
        self,
        session_id: str,
        profile_id: str,
        cdp_port: int,
        profile_dir: str | None = None,
        executable: str | None = None,
        headless: bool = False,
        start_url: str | None = None,
        observe_session_page=object,
        resolve_credentials=None,
        resolve_otp_secret=None,
        captcha_api_key: str | None = None,
        resolve_proxy=None,
        resolve_current_password=None,
        resolve_credential_change=None,
        store_credential_change=None,
        report_event=None,
        on_browser_closed=None,
        interval: float = 1.0,
        settle_seconds: float = 5.0,
        connect_timeout: float = 12.0,
        purpose: str = "login",
        contacts_csv_path: str | None = None,
        addon_candidates: list[dict[str, str]] | None = None,
        mail_workflow: dict[str, object] | None = None,
        alerts_topics: list[str] | None = None,
        newsletter_config: dict[str, object] | None = None,
        account_preferences: dict[str, object] | None = None,
    ) -> None:
        self.session_id = session_id
        self.profile_id = profile_id
        self.cdp_port = cdp_port  # kept for backward compatibility; unused
        self.profile_dir = profile_dir
        self.executable = executable
        self.headless = headless
        self.start_url = start_url or "https://accounts.google.com/"
        self._observe_session_page = observe_session_page
        self._resolve_credentials = resolve_credentials
        self._resolve_otp_secret = resolve_otp_secret
        self._captcha_api_key = captcha_api_key
        self._captcha_attempt: dict[str, object] | None = None
        self._captcha_attempted_widgets: set[tuple[str, str, str]] = set()
        self._resolve_proxy = resolve_proxy
        self._resolve_current_password = resolve_current_password
        self._resolve_credential_change = resolve_credential_change
        self._store_credential_change = store_credential_change
        self._report_event = report_event
        self._on_browser_closed = on_browser_closed
        self._interval = interval
        self._settle_seconds = settle_seconds
        self._connect_timeout = connect_timeout
        self.purpose = purpose
        self.contacts_csv_path = contacts_csv_path
        self.addon_candidates = addon_candidates or []
        self.mail_workflow = mail_workflow or {}
        self.alerts_topics = alerts_topics or []
        self.newsletter_config = newsletter_config or {}
        self._newsletter_switch_lock = threading.Lock()
        self._pending_newsletter_switch: dict[str, object] | None = None
        self.account_preferences = account_preferences or {}
        agent_settings = self.newsletter_config.get("agent_settings")
        configured_mode = str(self.newsletter_config.get("execution_mode") or "")
        if configured_mode not in {SKILL_REPLAY, DETERMINISTIC, AGENT_LEARNING, LIVE_FORM, MANUAL}:
            configured_mode = AGENT_LEARNING if self.newsletter_config.get("signup_mode") == "ADAPTIVE_LEARNING" else DETERMINISTIC
        self._newsletter_execution_mode = configured_mode
        self._newsletter_adaptive_learning = configured_mode == AGENT_LEARNING
        agent_settings = agent_settings if isinstance(agent_settings, dict) else {}
        # A live-form run learns the same way a deterministic one does: the
        # fills it resolves become a replay skill, so the next run for this
        # newsletter never has to introspect the page again.
        self._newsletter_skill_recording_enabled = bool(configured_mode in {AGENT_LEARNING, DETERMINISTIC, LIVE_FORM} and not self.newsletter_config.get("learned_skill"))
        self._newsletter_submit_attempted = False
        self._newsletter_enter_submit_attempted = False
        self._newsletter_followup_submit_attempted = False
        self._newsletter_fields_filled = False
        self._newsletter_response_listener_attached = False
        self._newsletter_submission_response = False
        self._newsletter_pre_submit_body = ""
        self._newsletter_step_signatures: set[str] = set()
        self._newsletter_step_ai_calls = 0
        self._newsletter_adaptive_continuations = 0
        self._newsletter_challenge_listener_attached = False
        self._newsletter_challenge_reason: str | None = None
        self._newsletter_challenge_status = NO_CHALLENGE
        self._newsletter_challenge_suspicious_reported = False
        self._newsletter_challenge_since: float | None = None
        self._newsletter_manual_review_reported = False
        self._newsletter_verify_not_before = 0.0
        self._newsletter_last_submit_at: float | None = None
        self._newsletter_started_at: float | None = None
        # A signup page may render its email field only after the visitor picks
        # a newsletter first. The reviewed entry step is clicked at most once
        # per session, so this guard is set before the click for the same
        # reason as ``_newsletter_submit_attempted``.
        self._newsletter_entry_clicked = False
        self._newsletter_entry_at: float | None = None
        self._newsletter_popup_actions = 0
        self._newsletter_last_url = self.start_url
        self._confirmation_tick_count = 0
        self._confirmation_search_submitted = False
        self._confirmation_message_opened = False
        self._confirmation_link_opened = False
        self._confirmation_started_at: float | None = None
        self._confirmation_wait_ticks = 0
        self._confirmation_followup_steps = 0
        self._confirmation_link_url: str | None = None
        self._newsletter_agent_attempts: dict[str, int] = {}
        self._newsletter_agent_locators: dict[str, object] = {}
        self._newsletter_agent_plan_loaded = False
        self._newsletter_agent_plan_signature = ""
        self._newsletter_agent_plan_calls = 0
        self._newsletter_agent_plan: dict[str, object] | None = None
        self._newsletter_agent_plan_meta: dict[str, object] = {}
        self._newsletter_agent_status_reported = False
        self._newsletter_agent_not_needed_reported = False
        # The optional browser-use resolver runs at most once per session, and
        # only after every other resolution path has failed.
        self._newsletter_browser_use_attempted = False
        self._newsletter_skill_actions: list[dict[str, object]] = []
        self._newsletter_skill_replay_reported = False
        self._newsletter_skill_replayed = False
        self._alerts_tick_count = 0
        self._alerts_current_index = 0
        self._alerts_created: list[str] = []
        self._alerts_wait_ticks = 0
        self._alerts_submit_signature: str | None = None
        self._alerts_typed_signature: str | None = None
        self._alerts_click_not_before = 0.0
        self._alerts_verify_not_before = 0.0
        self._alerts_next_topic_at = 0.0
        self._contacts_imported = False
        self._contacts_uploaded = False
        self._contacts_confirm_clicked = False
        self._contacts_post_confirm_ticks = 0
        self._contacts_confirm_retries = 0
        self._contacts_open_clicked = False
        self._contacts_tick_count = 0
        self._contacts_upload_tick = 0
        self._mail_tick_count = 0
        self._mail_search_wait_ticks = 0
        self._mail_search_submitted = False
        self._mail_selection_done = False
        self._mail_selection_attempts = 0
        self._mail_action_clicked = False
        self._mail_followup_confirmed = False
        self._mail_action_wait_ticks = 0
        self._mail_verification_wait_ticks = 0
        self._mail_match_count = 0
        self._mail_completed = False
        self._mail_reply_typed = False
        self._mail_reply_send_clicked = False
        self._mail_reply_confirmation_seen = False
        self._mail_reply_thread_opened = False
        self._mail_reply_scrolled = False
        self._mail_review_ready = False
        self._mail_review_scan_ticks = 0
        self._mail_review_stable_ticks = 0
        self._mail_review_last_summary: tuple[int, int, int, int] | None = None
        self._mail_review_scanned = False
        self._mail_review_opened_thread_ids: set[str] = set()
        self._mail_review_processed_count = 0
        self._mail_review_candidate_count = 0
        self._mail_review_unrendered_count = 0
        self._mail_review_visible_control_count = 0
        self._mail_review_button_count = 0
        self._mail_review_text_link_count = 0
        # The matched threads are located once from the Gmail result list and
        # then opened one at a time by route. Gmail removes a message from a
        # read-state filtered result as soon as it is opened, so re-running the
        # search between messages would lose the remaining matches.
        self._mail_review_candidates: list[dict[str, str]] = []
        self._mail_review_candidate_keys: set[str] = set()
        self._mail_review_collection_done = False
        self._mail_review_collection_stall_ticks = 0
        self._mail_review_collection_ticks = 0
        self._mail_review_route_wait_ticks = 0
        self._mail_review_empty_streak = 0
        self._mail_review_visible_rows = 0
        self._mail_review_empty_reason = ""
        self._mail_review_scrolls = 0
        self._mail_review_pages_advanced = 0
        self._mail_review_current_key: str | None = None
        self._mail_review_control_clicked = False
        self._mail_review_awaiting_landing = False
        self._mail_review_in_flight = False
        self._mail_review_landing_page = None
        self._mail_review_landing_host: str | None = None
        self._mail_review_landing_steps = 0
        self._mail_review_landing_idle_ticks = 0
        self._mail_review_confirmed_count = 0
        self._mail_review_attention_count = 0
        self._mail_review_confirmation_steps = 0
        self._mail_review_gmail_page = None
        self._markers_verified = False
        self._labels_verified = False
        self._labels_markers_tick_count = 0
        self._labels_markers_completion_ticks = 0
        self._security_verified = False
        self._security_clicked = False
        self._security_tick_count = 0
        self._security_completion_ticks = 0
        self._preferences_tick_count = 0
        self._preferences_avatar_clicked = False
        self._preferences_avatar_selected = False
        self._preferences_avatar_editor_next_clicked = False
        self._preferences_avatar_commit_clicked = False
        self._preferences_avatar_saved = False
        self._preferences_theme_settings_opened = False
        self._preferences_theme_menu_opened = False
        self._preferences_theme_view_all = False
        self._preferences_theme_clicked = False
        self._preferences_theme_saved = False
        self._preferences_settle_ticks = 0
        self._addons_tick_count = 0
        self._addons_sidebar_opened = False
        self._addons_get_opened = False
        self._addons_plus_clicked = False
        self._addons_plus_wait_ticks = 0
        self._addons_plus_scan_ticks = 0
        self._addons_plus_scan_reported = False
        self._addons_plus_click_detail: dict[str, object] | None = None
        self._addons_marketplace_reported = False
        self._addons_search_attempted = False
        self._addons_listing_opened = False
        self._addons_install_attempted = False
        self._addons_current_index = 0
        self._addons_installed: list[str] = []
        self._addons_skipped: list[str] = []
        self._addons_wait_ticks = 0
        self._addons_permission_reported = False
        self._credential_tick_count = 0
        self._credential_material: dict[str, object] | None = None
        self._credential_material_tried = False
        self._password_sheet_written = False
        self._two_step_sheet_written = False
        self._password_change_clicked = False
        self._password_change_confirm_ticks = 0
        self._password_change_settle_ticks = 0
        self._password_change_confirmed = False
        self._recovery_change_confirmed = False
        self._recovery_saved = False
        self._two_step_ui_started = False
        self._two_step_key: str | None = None
        self._two_step_removed = False
        self._two_step_code_attempts = 0
        self._two_step_code_submitted = False
        self._two_step_done = False
        self._retry_requested = False
        self._retry_attempt = 0
        self._loading_signature: tuple[str, str, str] | None = None
        self._loading_since: float | None = None
        self._addons_missing_since: float | None = None
        self._start_navigation_attempts = 0
        self._empty_google_page_retries = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_observation: tuple[str, str, str, str] | None = None
        self._unknown_since: float | None = None
        self._unknown_url: str | None = None
        self._startup_page = None
        self._credentials: tuple[str, str] | None = None
        self._credentials_tried = False
        self._otp_secret: str | None = None
        self._otp_secret_tried = False
        self._auto_attempted_urls: set[str] = set()
        self._chooser_clicked_urls: set[str] = set()
        self._continue_clicked_urls: set[str] = set()
        self._submitted_urls: set[str] = set()
        self._submitted_credential_stages: set[str] = set()
        self._fill_failed_urls: dict[str, int] = defaultdict(int)
        self._otp_attempted_urls: set[str] = set()
        self._otp_failed_urls: dict[str, int] = defaultdict(int)
        self._otp_retry_counts: dict[str, int] = defaultdict(int)
        self._otp_submitted_at: dict[str, float] = {}
        self._otp_last_code_counters: dict[str, int] = {}
        self._reported_no_page = False
        self._logged_states: set[tuple[str, str]] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"browser-observer-{self.session_id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._captcha_attempt is not None:
            self._captcha_attempt["cancel"].set()
        # Account-not-found cleanup is triggered from the observer callback
        # itself. Never join the current thread; setting the stop event lets
        # the loop leave and the persistent context close in _run's finally.
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)

    def queue_newsletter_task_switch(self, config: dict[str, object]) -> bool:
        """Queue a next-site handoff for this same account browser context."""
        if self.purpose != "newsletter" or not isinstance(config, dict):
            return False
        with self._newsletter_switch_lock:
            self._pending_newsletter_switch = dict(config)
        return True

    def _take_newsletter_task_switch(self) -> dict[str, object] | None:
        with self._newsletter_switch_lock:
            config = self._pending_newsletter_switch
            self._pending_newsletter_switch = None
            return config

    def _reset_newsletter_task_state(self, config: dict[str, object]) -> None:
        self.newsletter_config = config
        self.start_url = str(config.get("signup_url") or "")
        mode = str(config.get("execution_mode") or "")
        if mode not in {SKILL_REPLAY, DETERMINISTIC, AGENT_LEARNING, LIVE_FORM, MANUAL}:
            mode = AGENT_LEARNING if config.get("signup_mode") == "ADAPTIVE_LEARNING" else DETERMINISTIC
        self._newsletter_execution_mode = mode
        self._newsletter_adaptive_learning = mode == AGENT_LEARNING
        self._newsletter_skill_recording_enabled = bool(mode in {AGENT_LEARNING, DETERMINISTIC, LIVE_FORM} and not config.get("learned_skill"))
        self._newsletter_submit_attempted = False
        self._newsletter_enter_submit_attempted = False
        self._newsletter_followup_submit_attempted = False
        self._newsletter_fields_filled = False
        self._newsletter_response_listener_attached = False
        self._newsletter_submission_response = False
        self._newsletter_pre_submit_body = ""
        self._newsletter_step_signatures = set()
        self._newsletter_step_ai_calls = 0
        self._newsletter_adaptive_continuations = 0
        self._newsletter_challenge_listener_attached = False
        self._newsletter_challenge_reason = None
        self._newsletter_challenge_status = NO_CHALLENGE
        self._newsletter_challenge_suspicious_reported = False
        self._newsletter_challenge_since = None
        self._newsletter_manual_review_reported = False
        self._newsletter_verify_not_before = 0.0
        self._newsletter_last_submit_at = None
        self._newsletter_started_at = None
        self._newsletter_entry_clicked = False
        self._newsletter_entry_at = None
        self._newsletter_popup_actions = 0
        self._newsletter_last_url = self.start_url
        self._newsletter_agent_attempts = {}
        self._newsletter_agent_locators = {}
        self._newsletter_agent_plan_loaded = False
        self._newsletter_agent_plan_signature = ""
        self._newsletter_agent_plan_calls = 0
        self._newsletter_agent_plan = None
        self._newsletter_agent_plan_meta = {}
        self._newsletter_agent_status_reported = False
        self._newsletter_agent_not_needed_reported = False
        self._newsletter_browser_use_attempted = False
        self._newsletter_skill_actions = []
        self._newsletter_skill_replay_reported = False
        self._newsletter_skill_replayed = False

    def _apply_newsletter_task_switch(self, context) -> bool:
        config = self._take_newsletter_task_switch()
        if config is None:
            return False
        previous_page = self._startup_page
        if previous_page is not None:
            try:
                previous_page.close()
            except Exception:  # noqa: BLE001 - closing the old publisher tab is best effort
                logger.debug("old newsletter page was already closed for session %s", self.session_id)
        self._reset_newsletter_task_state(config)
        try:
            page = context.new_page()
        except Exception as exc:  # noqa: BLE001 - preserve queue progress if a tab cannot be created
            reuse = self._emit(
                "newsletter.signup.manual_review",
                f"A fresh publisher tab could not be created in the existing browser session ({type(exc).__name__})",
                result="attention",
            )
            if not reuse:
                self._stop.set()
            return True
        self._startup_page = page
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001 - foregrounding is best effort
            pass
        if not _open_start_page(page, self.start_url, keep_alive=True):
            reuse = self._emit("newsletter.signup.manual_review", "The next publisher page could not be opened in the existing browser session", result="attention")
            if not reuse:
                self._stop.set()
            return True
        logger.info("session %s: continuing newsletter job in a new page at %s", self.session_id, self.start_url)
        return True

    def _emit(self, event: str, detail: str = "", result: str = "success", metadata: dict[str, object] | None = None) -> bool:
        if self._report_event is None:
            return False
        try:
            try:
                response = self._report_event(self.session_id, event, detail, result, metadata)
            except TypeError:
                # Keep older test doubles and integrations compatible while
                # allowing the service callback to persist bounded metadata.
                response = self._report_event(self.session_id, event, detail, result)
            return response is True
        except Exception:  # noqa: BLE001 - event reporting must never kill the observer
            logger.exception("observer event report failed for session %s", self.session_id)
            return False

    def _record_newsletter_skill_action(self, action: dict[str, object]) -> None:
        """Record only replay-safe control facts; never record submitted values."""
        if not self._newsletter_skill_recording_enabled or not isinstance(action, dict) or len(self._newsletter_skill_actions) >= 24:
            return
        safe: dict[str, object] = {}
        for key in ("kind", "target", "field_key", "selector"):
            value = action.get(key)
            if isinstance(value, str) and value:
                safe[key] = value[:300]
        control = action.get("control")
        if isinstance(control, dict):
            safe["control"] = {
                key: str(control[key])[:160]
                for key in ("tag", "type", "id", "name", "aria_label", "title", "placeholder", "text")
                if control.get(key) not in (None, "")
            }
        if safe.get("kind") in {"locate", "fill", "click", "check"} and safe.get("target"):
            self._newsletter_skill_actions.append(safe)

    def _newsletter_skill_payload(self) -> dict[str, object] | None:
        if not self._newsletter_skill_recording_enabled or not self._newsletter_skill_actions:
            return None
        host = (urlparse(self._newsletter_last_url or self.start_url).hostname or "").lower().rstrip(".")
        return {
            "schema_version": 1,
            "source": self._newsletter_execution_mode.lower(),
            "website_host": host,
            "actions": list(self._newsletter_skill_actions),
        }

    def _notify_browser_closed(self) -> None:
        if self._on_browser_closed is None:
            return
        try:
            self._on_browser_closed(self.session_id)
        except Exception:  # noqa: BLE001 - cleanup reporting must not mask shutdown
            logger.exception("browser-close cleanup failed for session %s", self.session_id)

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragmatic fallback: no observer, session is human hand-off
            logger.warning("playwright not installed; session %s is human hand-off", self.session_id)
            return
        with sync_playwright() as pw:
            retry_count = 0
            while True:
                self._retry_requested = False
                context = None
                for launch_attempt in range(1, _BROWSER_LAUNCH_RETRY_LIMIT + 1):
                    if self._stop.is_set():
                        break
                    context = _launch_profile_context(pw, self)
                    if context is not None:
                        break
                    if launch_attempt < _BROWSER_LAUNCH_RETRY_LIMIT:
                        logger.warning(
                            "session %s: isolated browser launch failed; retrying %d/%d",
                            self.session_id,
                            launch_attempt,
                            _BROWSER_LAUNCH_RETRY_LIMIT - 1,
                        )
                        self._stop.wait(_BROWSER_LAUNCH_RETRY_DELAY_SECONDS)
                if context is None:
                    failure_event = _OPERATION_FAILURE_EVENTS.get(self.purpose, "failed")
                    self._emit(
                        failure_event,
                        "Could not launch the isolated browser after three attempts; verify the saved profile, browser executable, and assigned proxy if configured",
                        result="failure",
                    )
                    break
                try:
                    # Start each run in a fresh tab instead of reusing a
                    # restored or stale page from the persistent profile.
                    page = context.new_page()
                    self._startup_page = page
                    try:
                        page.bring_to_front()
                    except Exception:  # noqa: BLE001 - foregrounding is best effort
                        logger.debug("could not foreground startup page for session %s", self.session_id)
                    self._start_navigation_attempts = 1
                    if not _open_start_page(page, self.start_url, keep_alive=True):
                        failure_event = _OPERATION_FAILURE_EVENTS.get(self.purpose)
                        if failure_event:
                            self._emit(
                                failure_event,
                                "The isolated browser could not load the operation start page; verify the saved profile and assigned proxy if configured",
                                result="failure",
                            )
                        self._stop.set()
                    else:
                        # A saved Chrome profile can retain a blank startup tab
                        # beside the real account page. Once the intended page
                        # has a URL, close only those blank extras so the
                        # observer cannot later latch onto an empty session.
                        _close_blank_pages(context, keep=page)
                        page_count = len(context.pages)
                        logger.info("AUTOMATION ACTIVE: observer launched the isolated browser for session %s (%d page target(s))", self.session_id, page_count)
                        try:
                            for target in context.pages:
                                logger.info("session %s: page target %s", self.session_id, _open_page_url(target))
                        except Exception:  # noqa: BLE001 - report-only
                            logger.warning("session %s: page targets unreadable", self.session_id)
                        try:
                            while not self._stop.is_set():
                                try:
                                    switched = self._apply_newsletter_task_switch(context)
                                    if self._stop.is_set():
                                        break
                                    if switched:
                                        continue
                                    self._tick(context)
                                except Exception as exc:  # noqa: BLE001 - a bad page must not kill the observer
                                    message = str(exc).lower()
                                    if type(exc).__name__ == "TargetClosedError" or (
                                        "target page, context or browser has been closed" in message
                                    ):
                                        logger.info("observer target closed for session %s", self.session_id)
                                        self._stop.set()
                                    else:
                                        logger.exception("observer tick failed for session %s", self.session_id)
                                self._stop.wait(self._interval)
                        finally:
                            logger.info("observer closing the isolated browser for session %s", self.session_id)
                finally:
                    try:
                        # Closing the persistent context terminates the browser
                        # but keeps the user-data dir on disk, so the account's
                        # saved cookies/profile persist for the next attempt.
                        context.close()
                    except Exception:  # noqa: BLE001
                        logger.debug("context close failed for session %s", self.session_id, exc_info=True)

                if not self._retry_requested:
                    break
                if retry_count >= _ADDONS_POPUP_RETRY_LIMIT:
                    self._retry_requested = False
                    self._emit("addons.failed", "The add-on popup stayed loading after the retry limit; the session was closed", result="failure")
                    break
                retry_count += 1
                self._retry_attempt = retry_count
                self._reset_after_popup_retry()
                self._stop.clear()
                logger.warning("session %s: closed stalled add-on popup; retrying fresh browser attempt %d/%d", self.session_id, retry_count, _ADDONS_POPUP_RETRY_LIMIT)
                time.sleep(min(2.0, max(0.25, self._interval * 2)))
            # Notify only after the final context closes. Intermediate retry
            # cycles must not make the API briefly release the profile lock.
            self._notify_browser_closed()

    def _tick(self, browser: "Browser | BrowserContext") -> None:
        if self.purpose in {"newsletter", "newsletter_confirm"}:
            started_at = self._newsletter_started_at
            if started_at is None:
                started_at = time.monotonic()
                self._newsletter_started_at = started_at
            if time.monotonic() - started_at >= _NEWSLETTER_TASK_TIMEOUT_SECONDS:
                event = "newsletter.confirmation.manual_review" if self.purpose == "newsletter_confirm" else "newsletter.signup.manual_review"
                _finish_newsletter(self, event, "This newsletter page exceeded the 3-minute task limit; this item was stopped for review so the job can continue", result="attention")
                return
        if _handle_addons_missing_surface(self, browser):
            return
        page = _active_page(browser, self.purpose, preferred=self._startup_page)
        if page is None:
            if not self._reported_no_page:
                logger.warning("observer found no open page for session %s; pausing for the browser window", self.session_id)
                self._reported_no_page = True
            return
        url = page.url or ""
        if not url or url.startswith(_BLANK_URL_PREFIXES):
            if self._start_navigation_attempts < _BLANK_PAGE_RETRY_LIMIT:
                self._start_navigation_attempts += 1
                previous_page = page
                if self._start_navigation_attempts > 1:
                    # A timed-out navigation can leave Chromium's original
                    # target stuck in an in-flight request. Retry in a new tab
                    # and move the observer's target with it; closing the
                    # stuck tab is safe only after the replacement exists.
                    logger.info("session %s: retrying initial navigation attempt %d in a fresh tab", self.session_id, self._start_navigation_attempts)
                    page = _retry_start_in_fresh_tab(self, browser, previous_page)
                else:
                    logger.info("session %s: retrying initial navigation attempt %d to %s", self.session_id, self._start_navigation_attempts, self.start_url)
                    _open_start_page(page, self.start_url)
                return
            failure_event = _OPERATION_FAILURE_EVENTS.get(self.purpose)
            if failure_event:
                self._emit(failure_event, "The isolated browser could not load the operation start page; verify the saved profile and assigned proxy if configured", result="failure")
                self._stop.set()
            return
        self._start_navigation_attempts = 0
        title = page.title() or ""
        text = _visible_text(page)
        if text:
            self._empty_google_page_retries = 0
        if _handle_addons_loading_stall(self, page):
            return
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        challenge_hint = _login_challenge_hint(page, url, host)
        observed_text = f"{text}\n{challenge_hint}" if challenge_hint else text
        signal, action = classify_page(PageObservation(url=url, title=title, text=observed_text))

        newsletter_page_may_be_unknown = self.purpose in {"newsletter", "newsletter_confirm"}
        if signal.value == "UNKNOWN":
            page_settled = newsletter_page_may_be_unknown or _page_has_settled(self, url, text)
            if not newsletter_page_may_be_unknown and not page_settled:
                # The page engine is often still loading when the observer first
                # attaches. Judge it only after the settle window so opening the
                # browser never fails the session during a cold start.
                return
            if not newsletter_page_may_be_unknown and not text and host in {"accounts.google.com", "mail.google.com", "www.google.com"} and self._empty_google_page_retries < 2:
                self._empty_google_page_retries += 1
                _retry_start_in_fresh_tab(self, browser, page)
                return
            if newsletter_page_may_be_unknown:
                # Public newsletter sites are intentionally outside the Google
                # page taxonomy. Their pages must be handed to the discovered
                # newsletter profile instead of being treated as a login
                # failure; the profile-level handler owns the form timeout.
                self._unknown_since = None
                self._unknown_url = None
        else:
            self._unknown_since = None
            self._unknown_url = None

        observation_key = (url, title, signal.value, action.value)
        if self._observe_session_page is not object and observation_key != self._last_observation and not (newsletter_page_may_be_unknown and signal.value == "UNKNOWN"):
            try:
                self._observe_session_page(self.session_id, url, title, observed_text)
                self._last_observation = observation_key
            except Exception:  # noqa: BLE001
                self._stop.set()
                return

        if self._captcha_api_key and (signal == PageSignal.CAPTCHA or self.purpose == "newsletter"):
            if _handle_supported_captcha_tick(self, page, url):
                return

        # A Google recovery-email prompt can expose an email input too, but it
        # is a manual security step and must never receive the account login.
        if signal == PageSignal.CAPTCHA or action == PageAction.WAIT_FOR_HUMAN:
            # A visible challenge must stay in the same browser until the
            # operator completes it. A saved TOTP is the sole automatic
            # exception, and only on Google's Authenticator-code route.
            if signal.value == "MFA_OR_SECURITY_CHECK" and is_totp_challenge_url(url) and _has_otp_input(page) and self._resolve_otp_secret is not None:
                if _otp_should_attempt(page, self, url):
                    _fill_otp_code(page, self, url)
            state_key = (signal.value, url)
            if state_key not in self._logged_states:
                logger.info("session %s: %s requires verification in the open browser", self.session_id, signal.value)
                self._logged_states.add(state_key)
            return

        # The "Choose an account" page (remembered profile) carries no input
        # fields, so classify_page reports it as ORDINARY_INTERSTITIAL (the copy
        # contains "continue"). Drive consent, the chooser, and auto-fill from
        # what the page actually has on a Google login host, never off-host.
        if host in _LOGIN_HOSTS:
            if _has_credential_input(page):
                # Google can keep the same URL while moving from the email
                # step to password after a CAPTCHA. Let the form handler
                # distinguish those stages instead of using URL as a one-shot
                # guard, or the password step is skipped.
                if self._resolve_credentials is not None:
                    _fill_sign_in_form(page, self, url)
            elif url not in self._chooser_clicked_urls and _handle_account_chooser(page, self, url):
                self._chooser_clicked_urls.add(url)
            elif url not in self._continue_clicked_urls and _handle_google_account_continue(page, self, url, host):
                self._continue_clicked_urls.add(url)
            if is_totp_challenge_url(url) and _has_otp_input(page) and self._resolve_otp_secret is not None and _otp_should_attempt(page, self, url):
                _fill_otp_code(page, self, url)
        elif host in _ACCOUNT_HANDOFF_HOSTS and url not in self._continue_clicked_urls:
            # A saved profile can render the account hand-off inside Gmail or
            # My Account rather than on accounts.google.com. Only account-
            # specific wording is eligible on those already-authenticated
            # hosts, so ordinary Gmail buttons are never pressed.
            if _handle_google_account_continue(page, self, url, host):
                self._continue_clicked_urls.add(url)

        if _is_safe_interstitial(url, signal.value) and _can_click_consent(page):
            _click_consent(page)

        if self.purpose == "contacts" and not self._contacts_imported:
            _handle_contacts_tick(self, page, host, signal)
        if self.purpose == "labels_markers":
            _handle_labels_markers_tick(self, page, host)
        if self.purpose == "security":
            _handle_security_tick(self, page, host)
        if self.purpose == "account_preferences":
            _handle_account_preferences_tick(self, page, host)
        if self.purpose == "addons":
            addons_surface, addons_host = _active_addons_surface(page, host)
            _handle_addons_tick(self, addons_surface, addons_host)
        if self.purpose == "mail" and not self._mail_completed:
            _handle_mail_tick(self, page, host)
        if self.purpose == "alerts":
            _handle_alerts_tick(self, page, host)
        if self.purpose == "credential_change":
            _handle_credential_change_tick(self, page, host)
        if self.purpose == "newsletter" and not self._newsletter_submit_attempted:
            _handle_newsletter_signup_tick(self, page, host)
        elif self.purpose == "newsletter" and self._newsletter_submit_attempted:
            _verify_newsletter_signup_tick(self, page, host)
        if self.purpose == "newsletter_confirm":
            _handle_newsletter_confirmation_tick(self, page, host)

        state_key = (signal.value, url)
        if state_key in self._logged_states:
            return
        if signal.value == "CAPTCHA":
            logger.warning("session %s: anti-bot wall on %s; a human must pass it", self.session_id, url)
        elif signal.value == "MFA_OR_SECURITY_CHECK" and not (host in _LOGIN_HOSTS and _has_otp_input(page)):
            logger.info("session %s: second-factor prompt on %s awaiting manual confirmation", self.session_id, url)
        elif host in _LOGIN_HOSTS and _has_otp_input(page):
            logger.info("session %s: one-time code step on %s; auto sign-in is entering the TOTP", self.session_id, url)
        elif signal.value == "GMAIL_HOME":
            logger.info("session %s: mailbox detected on %s", self.session_id, url)
        elif signal.value == "GOOGLE_ACCOUNT_HOME":
            logger.info("session %s: signed-in Google Account page detected on %s", self.session_id, url)
        elif host in _LOGIN_HOSTS and _has_credential_input(page):
            logger.info("session %s: login form detected on %s; auto sign-in is entering the saved credentials", self.session_id, url)
        elif signal.value == "UNKNOWN" and not newsletter_page_may_be_unknown:
            logger.warning("session %s: page stayed unknown on %s; paused for a human", self.session_id, url)
        elif signal.value == "ORDINARY_INTERSTITIAL":
            logger.info("session %s: interstitial (consent/chooser) on %s", self.session_id, url)
            self._logged_states.add(state_key)

    def _reset_after_popup_retry(self) -> None:
        """Reset transient observer state while keeping installed add-ons.

        A retry starts a new persistent browser context against the same
        profile. Existing successful installs remain discoverable and are
        skipped by the normal Marketplace verification path.
        """
        self._last_observation = None
        self._unknown_since = None
        self._unknown_url = None
        self._startup_page = None
        self._loading_signature = None
        self._loading_since = None
        self._addons_missing_since = None
        self._reported_no_page = False
        self._logged_states.clear()
        self._continue_clicked_urls.clear()
        self._chooser_clicked_urls.clear()
        self._addons_sidebar_opened = False
        self._addons_get_opened = False
        self._addons_plus_clicked = False
        self._addons_plus_wait_ticks = 0
        self._addons_plus_scan_ticks = 0
        self._addons_plus_scan_reported = False
        self._addons_plus_click_detail = None
        self._addons_marketplace_reported = False
        self._addons_search_attempted = False
        self._addons_listing_opened = False
        self._addons_install_attempted = False
        self._addons_wait_ticks = 0
        self._addons_permission_reported = False


def _launch_profile_context(pw, observer: "BrowserObserver"):
    """Launch the account's own saved profile as a normal-looking Chrome window.

    Using Playwright's persistent context keeps the command line free of
    --remote-debugging-port and --enable-automation, which Chrome otherwise
    shows to Google and which make sign-in answer "this browser or app may not
    be secure". The profile dir is the same folder the app always used, so any
    cookies saved by an earlier sign-in survive and are reused later.
    """
    profile_dir = observer.profile_dir
    if not profile_dir:
        profile_dir = str((Path(get_settings().profile_root) / observer.profile_id).resolve())
    profile_path = Path(profile_dir)
    try:
        profile_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("observer could not create profile dir %s for session %s", profile_dir, observer.session_id)
        return None
    executable = observer.executable or resolve_browser_executable()
    if not executable:
        logger.warning("no browser executable found for session %s (set BROWSER_EXECUTABLE or install Chrome/Chromium)", observer.session_id)
        return None
    active_port_file = profile_path / "DevToolsActivePort"
    try:
        active_port_file.unlink(missing_ok=True)
    except OSError:
        pass
    logger.info("session %s: launching the saved profile with %s", observer.session_id, executable)
    launch_kwargs = {}
    if observer._resolve_proxy is not None:
        try:
            proxy = observer._resolve_proxy(observer.session_id)
        except Exception:  # noqa: BLE001 - never cross an assigned proxy boundary
            logger.exception("session %s: could not resolve its proxy; refusing direct fallback", observer.session_id)
            return None
        if proxy:
            launch_kwargs["proxy"] = proxy
    if not observer.headless:
        launch_kwargs["no_viewport"] = True
    # Playwright owns the initial page and requires navigation through the Page
    # API. Keep the normal Chrome window here so the account session retains
    # its address bar and in-window Google navigation; the observer performs
    # the explicit target navigation immediately after the context is ready.
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
    ]
    if observer.newsletter_config.get("ephemeral_browser"):
        # External-recipient runs must never inherit a saved Gmail session.
        # The directory is unique for the recipient and removed after close;
        # --incognito adds the browser-level boundary as well.
        launch_args.append("--incognito")
    try:
        return pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            executable_path=executable,
            headless=observer.headless,
            args=launch_args,
            ignore_default_args=["--enable-automation"],
            **launch_kwargs,
        )
    except Exception:  # noqa: BLE001
        logger.exception("observer failed to launch the browser for session %s", observer.session_id)
        return None


def _retry_start_in_fresh_tab(observer: "BrowserObserver", owner: "Browser | BrowserContext", previous_page: "Page") -> "Page":
    """Retry Google startup on a clean page and move observation to that tab."""
    try:
        contexts = list(owner.contexts) if hasattr(owner, "contexts") else [owner]
        if not contexts:
            return previous_page
        page = contexts[0].new_page()
        observer._startup_page = page
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001 - foregrounding is best effort
            pass
        _open_start_page(page, observer.start_url)
        try:
            if not previous_page.is_closed():
                previous_page.close()
        except Exception:  # noqa: BLE001 - replacement page is already running
            pass
        return page
    except Exception:  # noqa: BLE001 - keep the current tab as a fallback
        logger.warning("session %s: could not create a fresh retry tab", observer.session_id, exc_info=True)
        return previous_page


def _open_start_page(page: "Page", start_url: str, keep_alive: bool = False) -> bool:
    """Make the fresh window land on Google sign-in unless it already has a
    usable target (e.g. a profile that already progressed to the form)."""
    try:
        current = page.url or ""
    except Exception:  # noqa: BLE001
        current = ""
    try:
        if not current or current.startswith(_BLANK_URL_PREFIXES) or (urlparse(current).hostname or "") not in _LOGIN_HOSTS:
            # Google pages can keep network connections open through a proxy;
            # waiting for the document commit gives the observer a usable URL
            # without requiring every subresource to finish first.
            page.goto(start_url, wait_until="commit", timeout=15000)
            return True
        return True
    except Exception:  # noqa: BLE001 - a load failure must not kill the observer
        try:
            after_failure = page.url or ""
        except Exception:  # noqa: BLE001
            after_failure = ""
        logger.warning("observer could not open %s; current page=%s", start_url, after_failure or "about:blank", exc_info=True)
        # A slow proxy, a cold profile, or a newly-created popup can leave the
        # page blank for several ticks. Keep the observer alive so _tick can
        # retry navigation instead of closing the session immediately.
        return keep_alive or bool(after_failure and not after_failure.startswith(_BLANK_URL_PREFIXES))


def _open_page_url(page: "Page") -> str:
    try:
        return page.url or ""
    except Exception:  # noqa: BLE001 - a destroyed tab must not abort the observer
        return ""


def _close_blank_pages(owner: "Browser | BrowserContext", keep: "Page | None" = None) -> None:
    """Remove leftover blank tabs from the observer-owned profile context.

    Chrome may restore an empty first-run tab alongside the authenticated
    profile. It is safe to close only pages whose URL is explicitly blank; a
    real Gmail/Google Account tab is never discarded.
    """
    try:
        candidates = [page for context in owner.contexts for page in context.pages] if hasattr(owner, "contexts") else list(getattr(owner, "pages", None) or [])
    except Exception:  # noqa: BLE001 - the context may be closing
        return
    for page in candidates:
        page_url = _open_page_url(page)
        if page is keep or not page_url.startswith(_BLANK_URL_PREFIXES):
            continue
        try:
            page.close()
        except Exception:  # noqa: BLE001 - a detached startup tab is harmless
            logger.debug("could not close blank startup page", exc_info=True)


def _page_rank(url: str, purpose: str | None = None) -> int:
    if not url:
        return 3
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if purpose == "addons":
        # Marketplace installs can open Google's OAuth consent page as a
        # separate popup. It must win over the still-open Marketplace tab so
        # the observer can complete the install authorization there.
        if host == "accounts.google.com" and "/signin/oauth" in urlparse(url).path.lower():
            return 0
        if host == "workspace.google.com":
            return 1
        if host == "mail.google.com":
            return 2
        if host in _LOGIN_HOSTS:
            return 3
    if host in _LOGIN_HOSTS:
        return 0
    if host == "mail.google.com" or url.startswith(_BLANK_URL_PREFIXES):
        return 2
    return 1


def _handle_addons_loading_stall(observer: "BrowserObserver", page: "Page") -> bool:
    """Close and retry when an add-on Marketplace popup never finishes.

    This watchdog is deliberately narrow. It only watches the Marketplace
    route and Google's OAuth popup during the add-on operation, and only when
    the document is still loading, has no visible body, or displays an
    explicit loading/waiting state. A normal slow but usable page is left
    alone. The retry is bounded by ``_ADDONS_POPUP_RETRY_LIMIT`` in ``_run``.
    """
    if observer.purpose != "addons":
        return False
    try:
        url = _open_page_url(page)
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        path = parsed.path.lower()
        is_marketplace = host == "workspace.google.com" and "/marketplace" in path
        is_oauth_popup = host == "accounts.google.com" and "/signin/oauth" in path
        if not (is_marketplace or is_oauth_popup):
            observer._loading_signature = None
            observer._loading_since = None
            return False
        text = _visible_text(page).strip()
        try:
            ready_state = str(page.evaluate("document.readyState") or "").lower()
        except Exception:  # noqa: BLE001 - fake/closing pages may not evaluate
            ready_state = ""
        normalized_text = text.lower()
        crash_copy = any(term in normalized_text for term in (
            "aw, snap",
            "this page crashed",
            "page crashed",
            "page unresponsive",
            "la page a planté",
            "la page ne répond pas",
            "la pagina se ha bloqueado",
            "la página no responde",
        ))
        access_error = "403" in normalized_text or any(term in normalized_text for term in (
            "forbidden",
            "you don't have access to this page",
            "you do not have access to this page",
            "no tienes acceso a esta página",
            "no tienes acceso a esta pagina",
            "vous n'avez pas accès à cette page",
            "vous n'avez pas acces a cette page",
            "vous n’avez pas accès à cette page",
            "vous n’avez pas acces a cette page",
        ))
        loading_copy = any(term in normalized_text for term in (
            "loading",
            "chargement",
            "cargando",
            "please wait",
            "veuillez patienter",
            "espera un momento",
            "espera mientras",
        ))
        still_loading = access_error or crash_copy or ready_state in {"loading", "interactive"} or not text or loading_copy
        if not still_loading:
            observer._loading_signature = None
            observer._loading_since = None
            return False
        signature = (url, ready_state, text[:500])
        now = time.monotonic()
        if signature != observer._loading_signature:
            observer._loading_signature = signature
            observer._loading_since = now
            return False
        threshold = _ADDONS_POPUP_ERROR_SECONDS if access_error else (_ADDONS_POPUP_CRASH_SECONDS if crash_copy else _ADDONS_POPUP_STALL_SECONDS)
        if observer._loading_since is None or now - observer._loading_since < threshold:
            return False
        observer._retry_requested = True
        if access_error:
            observer._emit("addons.popup_error", "Google returned a 403 page for the add-on popup; closing the browser before retry", result="attention")
        else:
            observer._emit(
                "addons.popup_stalled",
                f"The add-on popup stayed in a loading state for {_ADDONS_POPUP_STALL_SECONDS:.0f} seconds; closing the browser before retry",
                result="attention",
            )
        observer._stop.set()
        return True
    except Exception:  # noqa: BLE001 - watchdog must never terminate observation itself
        logger.debug("add-on loading watchdog could not inspect session %s", observer.session_id, exc_info=True)
        return False


def _browser_pages(owner: "Browser | BrowserContext") -> list[object]:
    if hasattr(owner, "contexts"):
        return [page for context in owner.contexts for page in context.pages]
    return list(getattr(owner, "pages", None) or [])


def _addons_target_surface_exists(owner: "Browser | BrowserContext") -> bool:
    """Check whether the Marketplace page/panel still exists in the context."""
    for target in _browser_pages(owner):
        try:
            target_url = _open_page_url(target)
            parsed = urlparse(target_url)
            host = (parsed.hostname or "").lower().rstrip(".")
            if host == "workspace.google.com" and "/marketplace" in parsed.path.lower():
                return True
        except Exception:  # noqa: BLE001 - closing popup
            continue
        try:
            frames = getattr(target, "frames", [])
            if callable(frames):
                frames = frames()
            for frame in list(frames or []):
                frame_url = getattr(frame, "url", "")
                if callable(frame_url):
                    frame_url = frame_url()
                frame_host = (urlparse(frame_url or "").hostname or "").lower().rstrip(".")
                if frame_host == "workspace.google.com":
                    return True
                body = frame.locator("body")
                if body.count():
                    body_text = (body.inner_text(timeout=500) or "").lower()
                    if "marketplace" in body_text and any(term in body_text for term in ("install", "installer", "instalar", "add-on", "addon")):
                        return True
        except Exception:  # noqa: BLE001 - detached frames are normal during popup transitions
            continue
    return False


def _handle_addons_missing_surface(observer: "BrowserObserver", owner: "Browser | BrowserContext") -> bool:
    """Retry when the expected Marketplace popup/panel disappears."""
    if observer.purpose != "addons" or not observer._addons_get_opened:
        observer._addons_missing_since = None
        return False
    if _addons_target_surface_exists(owner):
        observer._addons_missing_since = None
        return False
    now = time.monotonic()
    if observer._addons_missing_since is None:
        observer._addons_missing_since = now
        return False
    if now - observer._addons_missing_since < _ADDONS_POPUP_MISSING_SECONDS:
        return False
    observer._retry_requested = True
    observer._emit(
        "addons.popup_closed",
        f"The Marketplace popup disappeared for {_ADDONS_POPUP_MISSING_SECONDS:.0f} seconds; closing the browser before retry",
        result="attention",
    )
    observer._stop.set()
    return True


def _active_page(owner: "Browser | BrowserContext", purpose: str | None = None, preferred: "Page | None" = None) -> "Page | None":
    if hasattr(owner, "contexts"):
        candidates = [page for context in owner.contexts for page in context.pages]
    else:
        candidates = list(getattr(owner, "pages", None) or [])
    if not candidates:
        return None
    if purpose in {"newsletter", "newsletter_confirm"}:
        # A publisher may open its signup dialog in a child tab/window. Prefer
        # the newest page that exposes an email control so the observer drives
        # the actual subscription surface instead of polling the landing page.
        for page in reversed(candidates):
            try:
                email_controls = page.locator(
                    'input[type="email"]:visible, input[autocomplete="email"]:visible, input[name*="email" i]:visible, input[id*="email" i]:visible'
                )
                if email_controls.count():
                    return page
            except Exception:  # noqa: BLE001 - a popup may detach between scans
                continue
    if preferred is not None and preferred in candidates:
        try:
            if not preferred.is_closed():
                return preferred
        except Exception:  # noqa: BLE001 - a closing tab is not a usable target
            pass
    if purpose == "account_preferences":
        # The profile-picture picker is sometimes promoted to a separate
        # Chrome tab/window instead of staying in the Personal info document.
        # Prefer the tab that actually contains the visible picker before the
        # normal URL ranking selects the still-open Personal info page.
        for page in reversed(candidates):
            try:
                text = _visible_text(page).casefold()
            except Exception:  # noqa: BLE001
                text = ""
            if re.search(_ACCOUNT_PREFERENCES_PROFILE_PICKER_MARKERS, text, re.IGNORECASE) or re.search(
                _ACCOUNT_PREFERENCES_THEME_PICKER_MARKERS, text, re.IGNORECASE
            ):
                return page
        if len(candidates) > 1:
            # A newly-created picker tab can be blank for one observer tick.
            # Give it the next tick rather than repeatedly clicking the
            # profile-picture control in the original page.
            return candidates[-1]
    # Prefer the sign-in tab: a remembered profile can leave a blank/first-run
    # window in another tab, and the automation must latch onto accounts.google.
    ranked = sorted(candidates, key=lambda page: _page_rank(_open_page_url(page), purpose))
    for page in ranked:
        if _open_page_url(page):
            return page
    return ranked[0]


def _active_addons_surface(page: "Page", page_host: str) -> tuple[object, str]:
    """Return the Marketplace frame when Gmail embeds it in the side panel.

    Gmail has used both a new tab/window and an embedded Workspace Marketplace
    frame over time. The observer receives the same outer Page in the latter
    case, so only inspecting ``page.url`` would leave the operation polling
    Gmail forever after the Marketplace was visibly opened.
    """
    if page_host == "workspace.google.com":
        return page, page_host
    try:
        frames = list(getattr(page, "frames", []) or [])
    except Exception:  # noqa: BLE001 - a page can be closing during hand-off
        frames = []
    for frame in frames:
        try:
            frame_url = getattr(frame, "url", "")
            if callable(frame_url):
                frame_url = frame_url()
            frame_host = (urlparse(frame_url or "").hostname or "").lower().rstrip(".")
            if frame_host == "workspace.google.com":
                return frame, frame_host

            # Gmail has also rendered the Marketplace in a same-origin-looking
            # embedded surface whose URL is not a stable workspace.google.com
            # URL. Use visible Marketplace copy as a second, content-based
            # signal so the observer does not keep polling the outer mailbox.
            body = frame.locator("body")
            if body.count():
                text = (body.inner_text(timeout=1000) or "").lower()
                marketplace_copy = "marketplace" in text and any(
                    term in text for term in ("install", "installer", "instalar", "add-on", "addon", "application")
                )
                if marketplace_copy:
                    return frame, "workspace.google.com"
        except Exception:  # noqa: BLE001 - ignore detached frames
            continue
    return page, page_host


def _page_has_settled(observer: "BrowserObserver", url: str, text: str) -> bool:
    now = time.monotonic()
    if observer._unknown_since is None or observer._unknown_url != url:
        observer._unknown_since = now
        observer._unknown_url = url
    if not url or url.startswith(_BLANK_URL_PREFIXES):
        return False
    if not text:
        # A committed URL with a permanently empty document can occur when a
        # proxy stalls or rejects a browser navigation. Do not leave the
        # operator staring at a blank window indefinitely.
        if now - observer._unknown_since >= _BLANK_CONTENT_TIMEOUT_SECONDS:
            logger.warning("session %s: page at %s stayed blank for %.0fs", observer.session_id, url, now - observer._unknown_since)
            return True
        return False
    settled = now - observer._unknown_since >= observer._settle_seconds
    if not settled:
        logger.debug("session %s: page unresolved on %s; inside settle window", observer.session_id, url)
    return settled


def _resolve_credentials(observer: "BrowserObserver") -> tuple[str, str] | None:
    if not observer._credentials_tried:
        observer._credentials_tried = True
        if observer._resolve_credentials is not None:
            try:
                observer._credentials = observer._resolve_credentials(observer.session_id)
            except Exception:  # noqa: BLE001
                logger.exception("credential resolution failed for session %s", observer.session_id)
    return observer._credentials


def _fill_sign_in_form(page: "Page", observer: "BrowserObserver", url: str) -> None:
    """Enter the stored identifier/password into Google's own sign-in form and
    submit each step. Only *visible* fields are touched (Google duplicates some
    inputs in the DOM). A transient failure mid-navigation is retried on the
    next tick instead of silently aborting the whole sign-in; a persistent one
    is reported and the URL is attempted once and then paused."""
    credentials = _resolve_credentials(observer)
    password_selector = "input[type=password]:visible, input[name=Passwd]:visible, input#password:visible"
    identifier_selector = "input[type=email]:visible, input[name=identifier]:visible, input[name=Email]:visible, input[id=identifierId]:visible"
    try:
        password_field = page.locator(password_selector)
        identifier_field = page.locator(identifier_selector)
        stage = "password" if password_field.count() else "identifier" if identifier_field.count() else "unknown"
    except Exception:  # noqa: BLE001 - page may be transitioning after verification
        return
    stage_key = f"{url}#{stage}"
    if stage_key in observer._submitted_credential_stages:
        return
    if credentials is None:
        observer._auto_attempted_urls.add(stage_key)
        observer._emit("skipped", "no stored credentials for account")
        return
    identifier, password = credentials
    failures = observer._fill_failed_urls.get(stage_key, 0)
    try:
        if password_field.count() and _input_value(password_field.first):
            _click_submit(page, url)
            observer._submitted_credential_stages.add(stage_key)
            observer._submitted_urls.add(url)
            return
        if identifier_field.count() and _input_value(identifier_field.first):
            _click_submit(page, url)
            observer._submitted_credential_stages.add(stage_key)
            observer._submitted_urls.add(url)
            return
        if password_field.count():
            password_field.first.fill(password, timeout=2000)
            observer._emit("password_filled", url)
            _click_submit(page, url)
            observer._submitted_credential_stages.add(stage_key)
            observer._submitted_urls.add(url)
            observer._auto_attempted_urls.add(stage_key)
            return
        if identifier_field.count():
            identifier_field.first.fill(identifier, timeout=2000)
            observer._emit("identifier_filled", url)
            _click_submit(page, url)
            observer._submitted_credential_stages.add(stage_key)
            observer._submitted_urls.add(url)
            observer._auto_attempted_urls.add(stage_key)
            return
    except Exception as exc:  # noqa: BLE001
        observer._fill_failed_urls[stage_key] = failures + 1
        logger.exception("auto sign-in attempt failed for session %s on %s (will retry)", observer.session_id, url)
        if failures + 1 >= 3:
            observer._auto_attempted_urls.add(stage_key)
            observer._emit("failed", f"persistent sign-in interaction failure on {url}: {exc}", result="failure")


def _has_credential_input(page: "Page") -> bool:
    """The identifier/password inputs are the one deterministic signal on a
    Google login page: placeholder text such as "Email or phone" is not exposed
    to innerText, so text heuristics alone can misclassify the page."""
    try:
        return page.locator(_CREDENTIAL_FIELD_SELECTOR).count() > 0
    except Exception:  # noqa: BLE001
        return False


def _has_otp_input(page: "Page") -> bool:
    try:
        return page.locator(_OTP_FIELD_SELECTOR).count() > 0
    except Exception:  # noqa: BLE001
        return False


def _resolve_otp_secret(observer: "BrowserObserver") -> str | None:
    if not observer._otp_secret_tried:
        observer._otp_secret_tried = True
        if observer._resolve_otp_secret is not None:
            try:
                observer._otp_secret = observer._resolve_otp_secret(observer.session_id)
            except Exception:  # noqa: BLE001
                logger.exception("OTP secret resolution failed for session %s", observer.session_id)
    return observer._otp_secret


def _otp_should_attempt(page: "Page", observer: "BrowserObserver", url: str) -> bool:
    """Allow a bounded retry when Google rejected or cleared a submitted code.

    TOTP values remain valid for a time window, so a retry is delayed until a
    fresh counter is available. This avoids submitting the same expired value
    repeatedly while also preventing an infinite retry loop.
    """
    if url not in observer._otp_attempted_urls:
        return True
    if observer._otp_retry_counts.get(url, 0) >= _OTP_MAX_RETRIES:
        return False
    submitted_at = observer._otp_submitted_at.get(url)
    if submitted_at is None or time.monotonic() - submitted_at < 4.0:
        return False
    try:
        page_text = _visible_text(page).lower()
        field = page.locator(_OTP_FIELD_SELECTOR)
        field_empty = field.count() > 0 and not _input_value(field.first)
    except Exception:  # noqa: BLE001 - the next observer tick will retry
        return False
    rejected = any(term in page_text for term in _OTP_REJECTION_TERMS)
    if not rejected and not field_empty:
        return False
    current_counter = int(time.time() // 30)
    last_counter = observer._otp_last_code_counters.get(url, -1)
    return current_counter > last_counter


def _fill_otp_code(page: "Page", observer: "BrowserObserver", url: str) -> None:
    """Generate a TOTP from the stored Authenticator secret and enter it into
    Google's code field. A transient failure is retried on the next tick; a
    persistent one is reported and the step is paused for the operator."""
    secret = _resolve_otp_secret(observer)
    if not secret:
        observer._otp_attempted_urls.add(url)
        observer._emit("skipped", "no stored 2FA secret for account")
        return
    failures = observer._otp_failed_urls.get(url, 0)
    try:
        code = generate_totp(secret)
    except Exception as exc:  # noqa: BLE001
        observer._otp_attempted_urls.add(url)
        observer._emit("failed", f"could not generate TOTP on {url}: {exc}", result="failure")
        return
    was_attempted = url in observer._otp_attempted_urls
    try:
        otp_field = page.locator(_OTP_FIELD_SELECTOR)
        if otp_field.count() and (not _input_value(otp_field.first) or was_attempted):
            if was_attempted:
                otp_field.first.fill("", timeout=2000)
            otp_field.first.fill(code, timeout=2000)
            observer._otp_retry_counts[url] = observer._otp_retry_counts.get(url, 0) + (1 if was_attempted else 0)
            observer._otp_submitted_at[url] = time.monotonic()
            observer._otp_last_code_counters[url] = int(time.time() // 30)
            observer._emit("otp_retry_filled" if was_attempted else "otp_filled", url)
            _click_submit(page, url)
            observer._otp_attempted_urls.add(url)
        elif otp_field.count():
            observer._otp_attempted_urls.add(url)
    except Exception as exc:  # noqa: BLE001
        observer._otp_failed_urls[url] = failures + 1
        logger.exception("OTP entry failed for session %s on %s (will retry)", observer.session_id, url)
        if failures + 1 >= 3:
            observer._otp_attempted_urls.add(url)
            observer._emit("failed", f"persistent OTP entry failure on {url}: {exc}", result="failure")


def _handle_account_chooser(page: "Page", observer: "BrowserObserver", url: str) -> bool:
    """The dedicated profile may already remember an account, so Google shows a
    "Choose an account" page without any input field. Expand it by clicking
    "Use another account"; the identifier form then appears on the same URL."""
    if _has_credential_input(page):
        return False
    clicked = _click_element_with_text(page, ("use another account",))
    if clicked:
        observer._emit("chooser_expanded", url)
    return clicked


_CONTINUE_HANDOFF_LABELS = (
    "continue",
    "continue as",
    "continuer",
    "continuer en tant que",
    "continuar",
    "continuar como",
)
_ACCOUNT_SPECIFIC_CONTINUE_LABELS = (
    "continue as",
    "continuer en tant que",
    "continuar como",
)


def _handle_google_account_continue(page: "Page", observer: "BrowserObserver", url: str, host: str | None = None) -> bool:
    """Advance Google's post-login account hand-off page.

    Google may retain hidden sign-in inputs after authentication and show a
    visible button such as ``Continue as`` / ``Continuer en tant que`` before
    handing the profile to Gmail. On login hosts, a plain Continue is also
    accepted for Google's sign-in hand-off. On Gmail/My Account, only the
    account-specific wording is accepted.
    """
    if _has_credential_input(page) or _has_otp_input(page):
        return False
    try:
        body_text = _visible_text(page).lower()
    except Exception:  # noqa: BLE001
        return False
    labels = _CONTINUE_HANDOFF_LABELS if host in _LOGIN_HOSTS else _ACCOUNT_SPECIFIC_CONTINUE_LABELS
    if not any(label in body_text for label in labels):
        return False
    try:
        for candidate in page.locator("button, [role=button], a").all():
            try:
                label = (candidate.inner_text() or "").strip().lower()
            except Exception:  # noqa: BLE001
                continue
            if any(label == wanted or label.startswith(wanted + " ") for wanted in labels):
                try:
                    candidate.click(timeout=1500)
                    observer._emit("account_continue_clicked", url)
                    logger.info("advanced Google account hand-off on %s", url)
                    return True
                except Exception:  # noqa: BLE001 - hidden/disabled; keep scanning
                    continue
    except Exception:  # noqa: BLE001
        return False
    return False


def _click_element_with_text(page: "Page", labels: tuple[str, ...]) -> bool:
    wanted = tuple(label.strip().lower() for label in labels)
    try:
        for candidate in page.locator("button, [role=button], a, li").all():
            try:
                label = (candidate.inner_text() or "").strip().lower()
            except Exception:  # noqa: BLE001
                continue
            if any(w in label for w in wanted):
                try:
                    candidate.click(timeout=1500)
                    return True
                except Exception:  # noqa: BLE001 - hidden/disabled; keep scanning
                    continue
    except Exception:  # noqa: BLE001
        return False
    return False


def _click_submit(page: "Page", url: str) -> None:
    try:
        for button in page.locator("button, [role=button], input[type=submit]").all():
            label = (button.inner_text() or "").strip().lower()
            if label in _SUBMIT_LABELS or any(word in label for word in _SUBMIT_LABELS):
                button.click(timeout=1500)
                logger.info("advanced sign-in step on %s", url)
                return
    except Exception:  # noqa: BLE001
        pass
    # Fallback used by the Google flow when the step has no labelled button:
    # the identifier/password field is focused after typing, so Enter submits.
    if not hasattr(page, "keyboard"):
        return
    try:
        page.keyboard.press("Enter")
        logger.info("signed in step on %s with keyboard Enter", url)
    except Exception:  # noqa: BLE001
        return


def _visible_text(page: "Page") -> str:
    try:
        return (page.locator("body").inner_text(timeout=1000)[:4000] if page.locator("body").count() else "")
    except Exception:  # noqa: BLE001
        return ""


def _handle_supported_captcha_tick(observer: BrowserObserver, page: "Page", url: str) -> bool:
    """Keep the observer responsive while one 2Captcha task is outstanding."""
    widget = detect_widget(page)
    attempt = observer._captcha_attempt
    if not widget:
        if attempt is not None:
            attempt["cancel"].set()
            observer._captcha_attempt = None
        return False
    kind, sitekey = widget
    if response_present(page, kind):
        if attempt is not None:
            attempt["cancel"].set()
            observer._captcha_attempt = None
        return False
    identity = (url, kind, sitekey)
    if attempt is not None and attempt["identity"] != identity:
        attempt["cancel"].set()
        attempt = None
        observer._captcha_attempt = None
    if attempt is None:
        if identity in observer._captcha_attempted_widgets:
            return False
        observer._captcha_attempted_widgets.add(identity)
        attempt = {"identity": identity, "cancel": threading.Event(), "token": None, "error": None, "applied": False}
        observer._captcha_attempt = attempt

        def solve() -> None:
            try:
                attempt["token"] = solve_widget(observer._captcha_api_key, kind, url, sitekey, attempt["cancel"])
            except Exception:  # noqa: BLE001 - provider errors fall back to manual handling
                attempt["error"] = "The CAPTCHA service could not solve this widget"

        threading.Thread(target=solve, name=f"captcha-{observer.session_id}", daemon=True).start()
        observer._emit("captcha_solver_started", "Supported CAPTCHA sent to 2Captcha")
        return True
    if attempt["error"]:
        return False  # existing manual-review path handles the unresolved page
    if attempt["applied"]:
        return False
    token = attempt["token"]
    if not token:
        return True
    if apply_token(page, kind, token):
        attempt["applied"] = True
        attempt["token"] = None
        observer._emit("captcha_solver_applied", "2Captcha widget token applied")
        return False
    attempt["error"] = "The CAPTCHA token could not be applied"
    return False


def _login_challenge_hint(page: "Page", url: str, host: str) -> str:
    """Add visible DOM evidence when Google's challenge has little body text."""
    if host not in _LOGIN_HOSTS:
        return ""
    try:
        body_text = _visible_text(page).casefold()
        recovery_terms = ("recovery email", "recovery e-mail", "recovery address", "adresse e-mail de récupération", "adresse email de récupération", "e-mail de récupération", "correo electrónico de recuperación")
        if any(term in body_text for term in recovery_terms):
            return "confirm your recovery email to get a verification code"
        # Some localized recovery screens render little body copy to the
        # accessibility tree but keep the instruction on the input itself.
        email_inputs = page.locator('input[type="email"]:visible')
        for index in range(min(email_inputs.count(), 4)):
            field = email_inputs.nth(index)
            field_context = " ".join(filter(None, (
                field.get_attribute("aria-label"), field.get_attribute("placeholder"),
                field.get_attribute("name"), field.get_attribute("id"),
                field.locator("xpath=..").inner_text(timeout=300),
            ))).casefold()
            if any(term in field_context for term in recovery_terms):
                return "confirm your recovery email to get a verification code"
    except Exception:  # noqa: BLE001 - page may still be rendering
        pass
    if "/challenge/pwd" in urlparse(url).path.lower():
        try:
            if page.locator("input[type=password]:visible, input[name=Passwd]:visible, input#password:visible").count():
                return "enter your password"
        except Exception:  # noqa: BLE001 - the challenge form may still be rendering
            pass
    try:
        if page.locator('iframe[src*="recaptcha" i]:visible, iframe[src*="hcaptcha" i]:visible, iframe[src*="captcha" i]:visible').count():
            return "captcha verification required"
    except Exception:  # noqa: BLE001 - a challenge iframe can detach while loading
        pass
    if "/challenge" in urlparse(url).path.lower() and not is_totp_challenge_url(url):
        try:
            if page.locator('input[autocomplete="tel"]:visible, input[name*="phone" i]:visible, input[id*="phone" i]:visible, input[aria-label*="phone" i]:visible').count():
                return "phone number verification required"
        except Exception:  # noqa: BLE001 - wait for a stable challenge render
            pass
    return ""


def _input_value(field) -> str:
    """Read a field's current value without aborting the sign-in step.

    The selector may match a wrapper element carrying a credential-looking id
    (Google wraps the password field in a div#password), which is not an
    <input> and makes Locator.input_value raise. Such a node simply counts as
    empty so the flow falls through to the real input and fills it."""
    try:
        return field.input_value(timeout=1000)
    except Exception:  # noqa: BLE001
        return ""


def _is_safe_interstitial(url: str, signal: str) -> bool:
    if signal != "ORDINARY_INTERSTITIAL":
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in {"accounts.google.com", "consent.google.com"}:
        return True
    # Google Workspace terms-of-service / signin speedbump pages.
    if host == "workspace.google.com" and "termsofservice" in (parsed.path or "").lower():
        return True
    return False


def _can_click_consent(page: "Page") -> bool:
    try:
        if _has_credential_input(page) or _has_otp_input(page):
            return False
        text = _visible_text(page)
        return any(word in text.lower() for word in ("terms", "privacy", "consent", "welcome", "understand", "continue"))
    except Exception:  # noqa: BLE001
        return False


def _click_consent(page: "Page") -> None:
    try:
        for button in page.locator("button, [role=button], a").all():
            try:
                label = (button.inner_text() or "").strip().lower()
            except Exception:  # noqa: BLE001
                continue
            if label in _SAFE_CONSENT_LABELS or any(word in label for word in ("i agree", "accept all", "i understand", "understand", "continuer", "continuar")):
                try:
                    button.click(timeout=1500)
                    logger.info("advanced consent on %s", page.url)
                    return
                except Exception:  # noqa: BLE001 - hidden/disabled button; keep scanning
                    continue
    except Exception:  # noqa: BLE001
        return


_CONTACTS_IMPORT_LABELS = (
    "import contacts",
    "import",
    "importer les contacts",
    "importer des contacts",
    "importer",
    "importar contactos",
    "importar contatos",
    "kontakte importieren",
    "importieren",
    "импорт контактов",
    "импорт",
)
_CONTACTS_FILE_BUTTON_LABELS = (
    "select a file",
    "select file",
    "choose file",
    "choose a file",
    "browse",
    "sélectionnez un fichier",
    "choisir un fichier",
    "parcourir",
    "seleccionar un archivo",
    "seleccionar archivo",
    "elegir un archivo",
    "examinar",
)
_CONTACTS_CONFIRM_LABELS = (
    "import",
    "import now",
    "import contacts",
    "importar",
    "importar ahora",
    "importar contactos",
    "upload",
    "subir",
    "open",
    "abrir",
    "ok",
    "accept",
    "aceptar",
    "confirm",
    "confirmar",
    "importer",
    "ouvrir",
    "importer les contacts",
)
_CONTACTS_SETTINGS_LABELS = (
    "settings and help",
    "settings",
    "paramètres",
    "ajustes",
    "configuración",
    "einstellungen",
)
_CONTACTS_DONE_MARKERS = (
    "contacts imported",
    "contact imported",
    "imported successfully",
    "successfully imported",
    "import complete",
    "import terminée",
    "importation terminée",
    "importation réussie",
    "contacts importés",
    "contacts importés avec succès",
    "import terminé",
    "contactos importados",
    "contacto importado",
    "importado correctamente",
    "importados correctamente",
    "importación completa",
    "importación completada",
    "importación finalizada",
    "se importaron",
    "your import is complete",
    "the import is complete",
    "votre importation est terminée",
    "le processus est terminé",
    "processus terminé",
    "a été importé",
    "importé avec succès",
    "the process is complete",
    "the process has finished",
    "process completed",
    "el proceso ha terminado",
    "el proceso se completó",
)
_CONTACTS_MAX_TICKS = 90  # ~90 x 1.25s ≈ 2 minutes until the flow gives up
_CONTACTS_FINAL_IMPORT_SELECTOR = (
    "#yDmH0d > div.uW2Fw-Sx9Kwc.uW2Fw-Sx9Kwc-OWXEXe-n2to0e.uW2Fw-qON5Qe-FoKg4d-Sx9Kwc-fZiSAe.yaahMe.GaqAuc."
    "uW2Fw-Sx9Kwc-OWXEXe-FNFY6c > div.uW2Fw-wzTsW > div > div.uW2Fw-T0kwCb > div:nth-child(2) > button"
)

_GMAIL_MARKERS_SETTINGS_URL = "https://mail.google.com/mail/u/0/?tab=rm&ogbl#settings/inbox"
_GMAIL_LABELS_SETTINGS_URL = "https://mail.google.com/mail/u/0/#settings/labels"
_GOOGLE_ENHANCED_SAFE_BROWSING_URL = "https://myaccount.google.com/account-enhanced-safe-browsing"
_SAFE_BROWSING_PAGE_PATH = "/account-enhanced-safe-browsing"
_SAFE_BROWSING_TERMS = (
    "enhanced safe browsing",
    "use enhanced safe browsing",
    "enhanced protection",
    "navigation sécurisée améliorée",
    "navigation securisee amelioree",
    "protection renforcée",
    "protection renforcee",
    "navegación segura mejorada",
    "navegacion segura mejorada",
    "protección mejorada",
    "proteccion mejorada",
)
_SAFE_BROWSING_ACTIVATE_LABELS = (
    "turn on",
    "enable",
    "activate",
    "keep on",
    "keep it on",
    "activer",
    "garder activé",
    "maintenir activé",
    "activar",
    "mantener activado",
    "habilitar",
)
_GMAIL_MARKER_SECTION_LABELS = (
    "importance markers",
    "marqueurs d'importance",
    "marqueurs d’importance",
    "marcadores de importancia",
)
_GMAIL_SHOW_MARKER_LABELS = (
    "show markers",
    "afficher les marqueurs",
    "mostrar marcadores",
)
_GMAIL_NO_MARKER_LABELS = ("no markers", "aucun marqueur", "sin marcadores")
_GMAIL_SYSTEM_LABEL_SECTION_LABELS = (
    "system labels",
    "libellés système",
    "etiquetas del sistema",
)
_GMAIL_SHOW_LABELS = ("show", "afficher", "mostrar")
_GMAIL_HIDE_LABELS = ("hide", "masquer", "ocultar")
_GMAIL_SAVE_LABELS = (
    "save changes",
    "enregistrer les modifications",
    "guardar cambios",
)


def _handle_contacts_tick(observer: "BrowserObserver", page: "Page", host: str, signal) -> None:
    """Drive the Google Contacts import inside a contacts-purpose session.

    Flow: click the import entry point, upload the rendered CSV through the
    dialog's file input, then wait for the success copy before stopping the
    observer. Any stop releases the browser so the session closes cleanly.
    """
    observer._contacts_tick_count += 1
    if observer._contacts_tick_count > _CONTACTS_MAX_TICKS:
        observer._emit("contacts.failed", "import flow timed out before completion", result="failure")
        observer._stop.set()
        return
    if host not in {"contacts.google.com"}:
        return
    if not observer._contacts_uploaded:
        _contacts_advance_file_flow(observer, page)
        return
    # Two-step dialogs stay open after the file is picked and need an explicit
    # "Import" confirmation; a single-attempt click covers that without looping.
    if not observer._contacts_confirm_clicked and observer._contacts_tick_count - observer._contacts_upload_tick >= 2:
        if _contacts_click_final_import(page) or _contacts_click_label(page, _CONTACTS_CONFIRM_LABELS):
            observer._contacts_confirm_clicked = True
            observer._emit("contacts.confirmed", "confirmed the contacts import dialog")
    if observer._contacts_confirm_clicked:
        observer._contacts_post_confirm_ticks += 1
        if observer._contacts_post_confirm_ticks in {2, 5, 10, 20}:
            logger.info(
                "session %s: contacts import settle tick=%s dialog_visible=%s final_text_visible=%s done_marker_visible=%s",
                observer.session_id,
                observer._contacts_post_confirm_ticks,
                _contacts_import_dialog_visible(page),
                _contacts_has_visible_exact_text(page, ("import", "importar", "importer")),
                _visible_text_mentions_done(page),
            )
        # A Google dialog can re-render the action node immediately after the
        # file upload. Retry the exact visible action text a small number of
        # times if it is still present; this remains bounded and does not
        # submit any other control.
        if observer._contacts_post_confirm_ticks in {5, 10, 20} and observer._contacts_confirm_retries < 3:
            if _contacts_click_final_import(page) or _contacts_click_label(page, ("import", "importar", "importer")):
                observer._contacts_confirm_retries += 1
                observer._emit("contacts.confirm_retry", "retried the exact contacts import action text")
    if (
        signal.value == "CONTACTS_IMPORT_DONE"
        or _visible_text_mentions_done(page)
        or _contacts_dialog_closed_after_confirm(observer, page)
        or _contacts_import_result_route(observer, page)
    ):
        observer._contacts_imported = True
        observer._emit("contacts.imported", f"Google Contacts import completed for {observer.contacts_csv_path or 'uploaded file'}")
        observer._stop.set()


_MAIL_MAX_TICKS = 120
_MAIL_SEARCH_WAIT_TICKS = 14
_MAIL_ACTION_SETTLE_TICKS = 3
# One matched email costs a bounded number of observer ticks: the thread opens,
# Gmail renders its body, the confirmation control is activated, and the page
# that control opens is driven through its own confirmation steps.
_MAIL_REVIEW_MAX_SCROLLS = 8
_MAIL_REVIEW_MAX_PAGES = 10
# Gmail needs a moment to render a search result list. The collection phase
# therefore waits for a settled list instead of concluding that the mailbox
# holds no match after the first empty scan.
_MAIL_REVIEW_COLLECTION_STALL_TICKS = 6
_MAIL_REVIEW_MIN_COLLECTION_TICKS = 10
# Gmail needs to leave the mailbox and settle its result list before either the
# matches or the absence of matches can be read from it.
_MAIL_REVIEW_ROUTE_WAIT_TICKS = 20
_MAIL_REVIEW_EMPTY_SETTLE_TICKS = 3
_MAIL_CONFIRMATION_CONTROL_WAIT_TICKS = 6
_MAIL_CONFIRMATION_LANDING_WAIT_TICKS = 12
_MAIL_CONFIRMATION_LANDING_SETTLE_TICKS = 6
_MAIL_CONFIRMATION_MAX_STEPS = 4
_MAIL_CONFIRMATION_TICKS_PER_MESSAGE = 70
# The UI permits 100 emails per account. Keep a finite observer budget for
# every selected message instead of capping a 100-message queue near message 28.
_MAIL_CONFIRMATION_MAX_TICKS = 7120
_MAIL_ACTION_LABELS = {
    "spam_to_inbox": (
        "not spam", "not a spam", "no es spam", "no es un spam",
        "pas de spam", "pas du spam", "pas un spam", "pas le spam",
        "no es correo no deseado", "no es spam", "non spam", "non-spam",
        "move to inbox", "move to the inbox", "mover a recibidos",
        "mover a bandeja de entrada", "déplacer vers la boîte de réception",
        "deplacer vers la boite de reception",
    ),
    "promotions_to_spam": ("report spam", "report as spam", "denunciar spam", "denunciar como spam", "signaler comme spam", "signaler comme indésirable", "reportar spam"),
    "reply": ("reply", "reply all", "respond", "respond to", "répondre", "repondre", "responder", "responder a", "antworten", "rispondi", "rispondere", "返信", "답장"),
}
_MAIL_REPLY_SEND_LABELS = (
    "send", "envoyer", "enviar", "enviar mensaje", "enviar e-mail", "senden", "invia", "inviare", "送信", "보내기",
)
_MAIL_CONTROL_SELECTOR = 'button, [role="button"], input[type="button"], input[type="submit"], [role="link"], a'
_MAIL_REPLY_SUCCESS_TERMS = (
    "message sent", "email sent", "mail sent", "mensaje enviado", "correo enviado",
    "message envoyé", "mail envoyé", "message envoye", "mail envoye", "nachricht gesendet",
    "messaggio inviato", "メッセージを送信しました", "メールを送信しました", "전송됨",
)
_MAIL_CONFIRMATION_CUE_TERMS = (
    "confirm", "confirmation", "verify", "verification", "activate", "activation",
    "subscribe", "subscription", "opt in", "opt-in", "join this list",
    "confirmer", "confirmation", "vérifier", "verifier", "valider", "abonner",
    "confirmar", "verificar", "suscribir", "suscribirse", "confirmacao",
    "bestätigen", "bestatigen", "verifizieren", "abonnieren", "aktivieren",
    "conferma", "verifica", "iscriviti", "подтверд", "подпис", "确认", "订阅", "確認", "登録", "확인", "구독",
)
# Confirmation controls are chosen from their rendered label only. No link
# target, message body, or attribute value is ever read, stored, or emitted.
_MAIL_MESSAGE_BODY_ROOT_SELECTORS = (
    '[role="main"] .a3s',
    '[role="main"] [data-message-id] .ii.gt',
    '[role="main"] .adn.ads .a3s',
)
_MAIL_CONFIRMATION_CONTROL_SELECTOR = 'a[href], button, [role="link"], [role="button"], input[type="submit"], input[type="button"]'
_MAIL_CONFIRMATION_CONTROL_LIMITS = (120, 60, 20)
_MAIL_MESSAGE_CONFIRMATION_MIN_SCORE = 60
_MAIL_LANDING_CONFIRMATION_MIN_SCORE = 40
_MAIL_LANDING_MAX_LABEL_WORDS = 8
_MAIL_CONFIRMATION_EXCLUSION_TERMS = (
    "unsubscribe", "unsubscribed", "opt out", "opt-out", "no thanks", "not now",
    "remind me later", "maybe later", "decline", "reject", "cancel", "stop emails",
    "delete", "report spam", "mark as spam", "not spam", "manage preferences",
    "update preferences", "preferences", "cookie", "settings", "sign in", "log in",
    "login", "password", "verify it's you", "verify it is you", "secure your account",
    "help", "support", "contact us", "view in browser", "view online", "open in browser",
    "print", "forward", "reply", "archive", "restore", "mark as read", "mark as unread",
    "report", "spam", "download", "track", "feedback", "skip", "back",
    "se désabonner", "desabonner", "se desabonner", "darse de baja", "abmelden",
)
_MAIL_CONFIRMATION_CONTROL_SCORES = (
    (
        (
            "confirm subscription", "confirm my subscription", "confirm your subscription",
            "confirm email", "confirm my email", "confirm your email", "confirm address",
            "confirm", "confirmer", "confirma", "confirmar", "bestätigen", "bestatigen",
            "подтвердить", "确认", "確認", "확인",
        ),
        100,
    ),
    (
        (
            "verify email", "verify my email", "verify your email", "verify address",
            "verify code", "verify", "activate your account", "activate account",
            "activate", "approve", "accept", "validate", "valider", "verifier",
            "verificar", "aktivieren", "aprobar", "activate account", "подтверждение",
        ),
        80,
    ),
    (
        (
            "subscribe", "subscription", "opt in", "opt-in", "join this list", "join",
            "sign me up", "abonner", "abonnement", "suscribir", "suscripción",
            "suscricao", "abonnieren", "iscriviti", "подписаться", "订阅", "登録", "구독",
        ),
        60,
    ),
    (
        (
            "continue", "next", "submit", "yes", "done", "proceed", "go",
            "continuer", "suivant", "continuar", "siguiente", "weiter", "avanti",
        ),
        40,
    ),
)
_MAIL_CONFIRMATION_BLOCKED_HOSTS = _LOGIN_HOSTS | {"mail.google.com", "myaccount.google.com"}
_MAIL_CONFIRMATION_SUCCESS_TERMS = (
    "subscription confirmed", "you are subscribed", "you’re subscribed", "you've been subscribed",
    "thanks for subscribing", "thank you for subscribing", "thanks for confirming",
    "successfully confirmed", "confirmation successful", "thanks for confirming",
    "thank you for confirming", "already confirmed", "you have confirmed",
    "your confirmation is complete", "confirmation complete", "confirmed successfully",
    "successfully activated", "successfully verified", "email confirmed", "address confirmed",
    "account confirmed", "activation complete", "subscription is now active",
    "you're all set", "you are all set",
    "inscription confirmée", "abonnement confirmé", "confirmation réussie",
    "votre adresse est confirmée", "adresse confirmée", "compte confirmé",
    "suscripción confirmada", "suscripcion confirmada", "correo confirmado", "cuenta confirmada",
    "inscrição confirmada", "bestätigung erfolgreich", "adresse bestätigt", "konto bestätigt",
    "iscrizione confermata", "abbonamento confermato", "conferma completata",
    "inschrijving bevestigd", "abonnement bevestigd", "bevestiging voltooid",
)
_MAIL_CONFIRMATION_FAILURE_TERMS = (
    "link expired", "link is no longer valid", "invalid confirmation", "confirmation failed",
    "not confirmed", "could not be confirmed", "couldn't be confirmed", "this link has expired",
    "the link has expired", "no longer valid", "invalid token", "something went wrong",
    "lien expiré", "lien invalide", "confirmation échouée", "enlace caducado", "enlace no válido",
    "une erreur est survenue", "se produjo un error", "etwas ist schiefgelaufen",
    "link scaduto", "conferma non riuscita", "link verlopen", "bevestiging mislukt",
)


def _mail_search_input(page: "Page"):
    selectors = (
        'input[aria-label*="Search mail" i], input[placeholder*="Search mail" i]',
        'input[aria-label*="Rechercher" i], input[placeholder*="Rechercher" i]',
        'input[aria-label*="Buscar" i], input[placeholder*="Buscar" i]',
    )
    for selector in selectors:
        try:
            field = page.locator(selector).first
            if field.count() and field.is_visible(timeout=500):
                return field
        except Exception:  # noqa: BLE001 - Gmail changes the shell while loading
            continue
    return None


def _mail_subject_query(config: dict[str, object]) -> str:
    # Subjects copied from a URL can contain encoded spaces such as ``%20``.
    # Gmail searches the decoded subject, not the literal escape sequence.
    subject = unquote(re.sub(r'["\r\n]+', " ", str(config.get("subject") or ""))).strip()
    subject = " ".join(subject.split())
    phrase = f'subject:"{subject}"' if subject else '{subject:subscription subject:newsletter subject:subscribe}'
    action = config.get("action")
    if action == "spam_to_inbox":
        source = "in:spam"
    elif action in {"reply", "confirmation_review"}:
        # Reply and review can target a message from any Gmail folder. The
        # same subject matcher is used below, but the query must not hide
        # Inbox, Promotions, Spam, or archived threads.
        source = "in:anywhere"
    else:
        source = "category:promotions"
    if action == "confirmation_review":
        # Confirmation review is intentionally constrained to one explicit
        # Gmail read state. Defaulting to unread prevents the queue from
        # reopening older messages unless an employee deliberately selects
        # the read-only review option.
        state = "is:read" if config.get("review_email_state") == "read" else "is:unread"
        return f"{source} {state} {phrase}"
    return f"{source} {phrase}"


def _mail_submit_search(observer: "BrowserObserver", page: "Page") -> bool:
    field = _mail_search_input(page)
    if field is None:
        return False
    try:
        field.fill(_mail_subject_query(observer.mail_workflow), timeout=1500)
        field.press("Enter", timeout=1500)
        return True
    except Exception:  # noqa: BLE001 - the field may be replaced mid-render
        return False


def _mail_search_applied(page: "Page", config: dict[str, object]) -> tuple[bool, str]:
    """Report whether Gmail is now showing the result list for the submitted query.

    Pressing Enter returns long before Gmail has left the mailbox it was on.
    Scanning for matches before the search route is live reads whatever list
    happens to be rendered, which is how an inbox row could be mistaken for a
    search result and why the previous run reported no match at all. The route
    is the primary signal; the applied query in Gmail's own search box is used
    when Gmail renders an unusual route.
    """
    try:
        fragment = (urlparse(str(_open_page_url(page) or "")).fragment or "").lower()
    except Exception:  # noqa: BLE001 - a page without a usable URL has no search route
        fragment = ""
    route = fragment.split("/", 1)[0] if fragment else ""
    if route.startswith("search"):
        return True, route
    query = _mail_subject_query(config).strip().lower()
    if not query:
        return False, route
    try:
        field = _mail_search_input(page)
        value = str(field.input_value(timeout=800) or "").strip().lower() if field is not None else ""
    except Exception:  # noqa: BLE001 - the search box can be replaced mid-render
        value = ""
    if query and query in value:
        return True, route or "search-box"
    return False, route


def _mail_select_subject_rows(page: "Page", config: dict[str, object]) -> int:
    try:
        return int(page.evaluate(
            """config => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const decodeSubject = value => {
                    try { return decodeURIComponent(value || ''); } catch (_) { return value || ''; }
                };
                const subjects = [...new Set([config.subject, decodeSubject(config.subject)])]
                    .map(normalize)
                    .filter(Boolean);
                const exact = config.match_mode === 'exact';
                const max = Math.max(1, Number(config.max_messages || 100));
                const rowSelectors = [
                    '[role="main"] tr.zA',
                    '[role="main"] tr',
                    '[role="main"] [role="row"]',
                    '[role="grid"] [role="row"]',
                    'tr.zA',
                    '.zA'
                ];
                const rows = [...new Set(rowSelectors.flatMap(selector => [...document.querySelectorAll(selector)]))];
                const used = new Set();
                let count = 0;
                const visible = node => {
                    const rect = node.getBoundingClientRect?.();
                    const style = getComputedStyle(node);
                    return !!rect && rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                for (const row of rows) {
                    if (!visible(row)) continue;
                    const rawText = [row.innerText || '', row.getAttribute('aria-label') || '', row.getAttribute('data-tooltip') || ''].join(' ');
                    const text = normalize(rawText);
                    const lines = (row.innerText || '').split(/\\n+/).map(normalize);
                    const match = exact
                        ? subjects.some(subject => lines.some(line => line === subject))
                        : subjects.some(subject => text.includes(subject));
                    if (!match) continue;
                    if (config.action === 'reply' || config.action === 'confirmation_review') {
                        // Reply is a thread-level action. The caller opens
                        // the matching subject cell with a trusted Playwright
                        // click; do not click the <tr> here because Gmail can
                        // interpret that delegated event as row selection.
                        return 1;
                    }
                    const checkbox = row.querySelector('[role="checkbox"], input[type="checkbox"], .T-Jo.J-J5-Ji');
                    if (!checkbox || used.has(checkbox) || !visible(checkbox)) continue;
                    used.add(checkbox);
                    const checked = checkbox.checked === true || checkbox.getAttribute('aria-checked') === 'true';
                    if (!checked) {
                        checkbox.click();
                        // Gmail uses a custom div for row selection. Only
                        // count a row once its selected state is observable.
                        const selected = checkbox.checked === true || checkbox.getAttribute('aria-checked') === 'true' || checkbox.classList.contains('T-Jo-J3-iE');
                        if (!selected) continue;
                    }
                    count += 1;
                    if (count >= max) break;
                }
                return count;
            }""",
            {"subject": unquote(str(config.get("subject") or "")), "match_mode": config.get("match_mode", "contains"), "max_messages": config.get("max_messages", 100)},
        ))
    except Exception:  # noqa: BLE001 - detached Gmail rows are expected during search
        return 0


def _mail_open_next_review_thread(page: "Page", config: dict[str, object], opened_thread_ids: set[str]) -> str | None:
    """Open one unvisited, subject-matched Gmail thread for a bounded review pass.

    Gmail thread identifiers stay only in the observer's in-memory set. They
    are never emitted, logged, or persisted. That lets a review queue return
    to the search results and skip threads already opened in the same run.
    """
    display_subject = " ".join(unquote(str(config.get("subject") or "")).split())
    subject = display_subject.lower()
    if not subject:
        return None
    exact = config.get("match_mode") == "exact"
    row_selectors = (
        '[role="main"] tr.zA',
        '[role="main"] tr',
        '[role="main"] [role="row"]',
        '[role="grid"] [role="row"]',
        'tr.zA',
        '.zA',
    )
    row_index = 0
    try:
        for selector in row_selectors:
            for row in page.locator(selector).all():
                try:
                    if not row.is_visible(timeout=300):
                        continue
                    thread_id = next((value for value in (
                        row.get_attribute("data-thread-id"),
                        row.get_attribute("data-legacy-thread-id"),
                        row.get_attribute("data-message-id"),
                    ) if value), None)
                    thread_key = f"gmail:{thread_id}" if thread_id else f"visible-row:{row_index}"
                    row_index += 1
                    if thread_key in opened_thread_ids:
                        continue
                    row_text = row.inner_text(timeout=600) or ""
                    text = " ".join(row_text.split()).lower()
                    lines = [line.strip().lower() for line in row_text.splitlines() if line.strip()]
                    if not (subject in lines if exact else subject in text):
                        continue
                    targets = row.locator('td.a4W, td.y6, .bog, [role="link"], a').all()
                    for target in targets:
                        try:
                            if target.is_visible(timeout=300):
                                target.click(timeout=2000)
                                return thread_key
                        except Exception:  # noqa: BLE001 - Gmail can replace a row during navigation
                            continue
                    for cell in row.locator('td').all():
                        try:
                            if not cell.is_visible(timeout=300):
                                continue
                            cell_text = " ".join((cell.inner_text(timeout=400) or "").split()).lower()
                            if subject in cell_text:
                                cell.click(timeout=2000)
                                return thread_key
                        except Exception:  # noqa: BLE001 - Gmail can replace a cell during navigation
                            continue
                except Exception:  # noqa: BLE001 - virtualized rows may detach between scans
                    continue
    except Exception:  # noqa: BLE001 - Gmail can replace the results while loading
        return None
    return None


def _mail_review_queue_mode(config: dict[str, object]) -> bool:
    return config.get("action") == "confirmation_review" and config.get("review_mode") == "queue"


def _mail_review_queue_limit(config: dict[str, object]) -> int:
    try:
        return max(1, min(100, int(config.get("max_messages") or 1)))
    except (TypeError, ValueError):
        return 1


def _mail_confirmation_tick_budget(config: dict[str, object]) -> int:
    """Allow the observer enough ticks to confirm every selected message.

    The previous fixed budget covered one opened message. A queue that opens
    and confirms several matched emails needs room for each message's render,
    click, and follow-up page steps before it gives up.
    """
    limit = _mail_review_queue_limit(config)
    return min(_MAIL_CONFIRMATION_MAX_TICKS, max(_MAIL_MAX_TICKS, _MAIL_CONFIRMATION_TICKS_PER_MESSAGE * limit + 120))


def _mail_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", " ".join(str(value or "").replace("\u00a0", " ").split())).casefold()
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _mail_confirmation_label_excluded(label: str) -> bool:
    normalized = _mail_match_text(label)
    return any(_mail_match_text(term) in normalized for term in _MAIL_CONFIRMATION_EXCLUSION_TERMS)


def _mail_confirmation_score(label: str) -> int:
    """Rank a rendered control label by how clearly it asks for confirmation."""
    normalized = _mail_match_text(label)
    padded = f" {normalized} "
    best = 0
    for terms, score in _MAIL_CONFIRMATION_CONTROL_SCORES:
        for term in terms:
            candidate = _mail_match_text(term)
            if not candidate:
                continue
            if normalized == candidate:
                best = max(best, score + 20)
            elif f" {candidate} " in padded:
                best = max(best, score + 10)
            elif candidate in normalized:
                best = max(best, score)
    return best


def _mail_control_kind(control) -> str:
    tag = ""
    try:
        tag = str(control.evaluate("(element) => (element && element.tagName ? element.tagName : '')") or "").lower()
    except Exception:  # noqa: BLE001 - a detached control has no tag to read
        tag = ""
    if tag in {"button", "input"}:
        return "button"
    attributes = {}
    for name in ("role", "type"):
        try:
            attributes[name] = str(control.get_attribute(name) or "").lower()
        except Exception:  # noqa: BLE001
            attributes[name] = ""
    if attributes.get("role") == "button" or attributes.get("type") in {"submit", "button"}:
        return "button"
    return "text_link"


def _mail_best_confirmation_control(page: "Page", root_selectors: tuple[str, ...], minimum_score: int) -> tuple[int, str, object] | None:
    """Return the strongest confirmation control rendered inside the given roots.

    Controls are matched on their visible label only. Opt-out, preference,
    sign-in, and Gmail chrome controls are never eligible, so an unsubscribe
    link can never be activated as a confirmation.
    """
    best: tuple[int, str, object] | None = None
    best_rank: tuple[int, int, int] | None = None
    order = 0
    for root_selector, control_limit in zip(root_selectors, _MAIL_CONFIRMATION_CONTROL_LIMITS):
        try:
            roots = page.locator(root_selector).all()
        except Exception:  # noqa: BLE001 - the message body may still be mounting
            continue
        for root in roots:
            try:
                controls = root.locator(_MAIL_CONFIRMATION_CONTROL_SELECTOR).all()[:control_limit]
            except Exception:  # noqa: BLE001 - Gmail can replace the rendered body
                continue
            for control in controls:
                order += 1
                try:
                    if not control.is_visible(timeout=300):
                        continue
                    label = _mail_control_label(control)
                except Exception:  # noqa: BLE001 - a control can detach between reads
                    continue
                if not label or _mail_confirmation_label_excluded(label):
                    continue
                if len(label.split()) > _MAIL_LANDING_MAX_LABEL_WORDS:
                    continue
                score = _mail_confirmation_score(label)
                if score < minimum_score:
                    continue
                kind = _mail_control_kind(control)
                rank = (score, 1 if kind == "button" else 0, -order)
                if best_rank is None or rank > best_rank:
                    best_rank = rank
                    best = (score, kind, control)
    return best


def _mail_click_confirmation_control(page: "Page") -> str | None:
    """Activate the confirmation button or clickable text inside the message."""
    match = _mail_best_confirmation_control(page, _MAIL_MESSAGE_BODY_ROOT_SELECTORS, _MAIL_MESSAGE_CONFIRMATION_MIN_SCORE)
    if match is None:
        return None
    _score, kind, control = match
    try:
        control.click(timeout=2500)
    except Exception:  # noqa: BLE001 - Gmail can re-render the body mid-click
        return None
    return kind


def _mail_review_candidate_rows(page: "Page", config: dict[str, object]) -> list[dict[str, str]]:
    """Collect the subject-matched threads currently rendered in Gmail's list.

    Only in-memory thread identifiers and Gmail's own thread routes are kept.
    Neither a subject line, a sender, nor a message body leaves this scan.
    """
    try:
        rows = page.evaluate(
            """config => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const decodeSubject = value => {
                    try { return decodeURIComponent(value || ''); } catch (_) { return value || ''; }
                };
                const subjects = [...new Set([config.subject, decodeSubject(config.subject)])]
                    .map(normalize)
                    .filter(Boolean);
                const exact = config.match_mode === 'exact';
                const autoDetect = !subjects.length;
                const rows = [...new Set([
                    '[role="main"] tr.zA',
                    '[role="main"] tr',
                    '[role="main"] [role="row"]',
                    '[role="grid"] [role="row"]',
                    'tr.zA',
                    '.zA'
                ].flatMap(selector => [...document.querySelectorAll(selector)]))];
                const visible = node => {
                    const rect = node.getBoundingClientRect?.();
                    const style = getComputedStyle(node);
                    return !!rect && rect.width > 0 && rect.height > 0
                        && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const found = [];
                const seen = new Set();
                for (const row of rows) {
                    if (!visible(row)) continue;
                    const rowText = row.innerText || '';
                    const text = normalize([rowText, row.getAttribute('aria-label') || ''].join(' '));
                    const lines = (rowText || '').split(/\\n+/).map(normalize);
                    const subjectNode = row.querySelector('.bog, .bqe, [data-testid="subject"]');
                    const visibleSubject = normalize(subjectNode?.innerText || '');
                    const match = autoDetect
                        ? !!visibleSubject
                            && /(subscription|subscribe|newsletter|mailing list|opt[ -]?in)/.test(visibleSubject)
                            && /(confirm|verify|activate|complete|finish|opt[ -]?in)/.test(visibleSubject)
                        : exact
                            ? subjects.some(subject => lines.some(line => line === subject))
                            : subjects.some(subject => text.includes(subject));
                    if (!match) continue;
                    const threadId = row.getAttribute('data-thread-id')
                        || row.getAttribute('data-legacy-thread-id')
                        || row.getAttribute('data-message-id') || '';
                    // Gmail's subject cell links to the thread route. Tracked
                    // and list-unsubscribe links never match this shape.
                    let href = '';
                    for (const anchor of [...row.querySelectorAll('a[href]')]) {
                        const value = (anchor.getAttribute('href') || '').trim();
                        if (!value || value === '#' || /unsubscribe/i.test(value)) continue;
                        if (/^#?[a-z]+\\/[A-Za-z0-9_-]{4,}/i.test(value) || /^\\/mail\\/u\\/\\d+\\//i.test(value)) {
                            href = value;
                            break;
                        }
                    }
                    if (!href) {
                        const route = (row.querySelector('a[href*="/inbox/"], a[href*="#all/"], a[href*="#search/"]') || {}).getAttribute?.('href');
                        if (route) href = route.trim();
                    }
                    const key = threadId
                        ? 'gmail:' + threadId
                        : 'row:' + normalize(href || text).slice(0, 120);
                    if (seen.has(key)) continue;
                    seen.add(key);
                    found.push({ key, href });
                }
                return found;
            }""",
            {"subject": unquote(str(config.get("subject") or "")), "match_mode": config.get("match_mode", "contains")},
        )
    except Exception:  # noqa: BLE001 - Gmail can replace the result list while loading
        return []
    if not isinstance(rows, list):
        return []
    candidates: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        if not key:
            continue
        candidates.append({"key": key, "href": str(row.get("href") or "").strip()})
    return candidates


def _mail_search_list_status(page: "Page") -> dict[str, object]:
    """Report whether Gmail's result list has rendered rows or matched nothing.

    The queue has to tell "this mailbox holds no matching email" apart from
    "the results have not rendered yet". Declaring the mailbox empty while the
    list is still loading is what made a search look empty seconds after it was
    submitted, so both signals are read from the same rendered list.
    """
    try:
        status = page.evaluate(
            """() => {
                const visible = node => {
                    const rect = node.getBoundingClientRect?.();
                    const style = getComputedStyle(node);
                    return !!rect && rect.width > 0 && rect.height > 0
                        && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const rows = [...new Set([
                    '[role="main"] tr.zA',
                    '[role="main"] tr',
                    '[role="main"] [role="row"]',
                    '[role="grid"] [role="row"]',
                    'tr.zA',
                    '.zA'
                ].flatMap(selector => [...document.querySelectorAll(selector)]))];
                let visibleRows = 0;
                for (const row of rows) {
                    if (visible(row)) visibleRows += 1;
                }
                const text = (document.querySelector('[role="main"]')?.innerText || '')
                    .replace(/\\s+/g, ' ').toLowerCase();
                const noResults = text.includes('no messages matched your search')
                    || text.includes('did not match any emails')
                    || text.includes('your search did not match');
                return { visibleRows, noResults };
            }"""
        )
    except Exception:  # noqa: BLE001 - Gmail can replace the result list while loading
        return {"visible_rows": 0, "no_results": False}
    if not isinstance(status, dict):
        return {"visible_rows": 0, "no_results": False}
    try:
        return {"visible_rows": max(0, int(status.get("visibleRows", 0))), "no_results": bool(status.get("noResults", False))}
    except (TypeError, ValueError):
        return {"visible_rows": 0, "no_results": False}


def _mail_review_scroll_results(page: "Page") -> bool:
    """Advance the Gmail result list so virtualized rows render more matches.

    A bounded number of passes lets a queue find matches that are not in the
    first rendered screen instead of only the rows Gmail happened to mount.
    """
    try:
        return bool(page.evaluate(
            """() => {
                const visible = node => {
                    const rect = node.getBoundingClientRect?.();
                    const style = getComputedStyle(node);
                    return !!rect && rect.width > 0 && rect.height > 0
                        && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const candidates = [...document.querySelectorAll('[role="main"], [role="main"] div, div, body')]
                    .filter(node => visible(node) && node.scrollHeight > node.clientHeight + 40)
                    .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
                const pane = candidates[0] || document.scrollingElement || document.documentElement;
                const before = pane.scrollTop || 0;
                const amount = Math.max(320, Math.floor((pane.clientHeight || window.innerHeight || 600) * 0.9));
                pane.scrollTop = Math.min(before + amount, pane.scrollHeight || before + amount);
                return pane.scrollTop > before;
            }"""
        ))
    except Exception:  # noqa: BLE001 - the list may be re-rendered while scrolling
        return False


def _mail_review_next_page(page: "Page") -> bool:
    """Advance a settled Gmail search page to collect another bounded batch."""
    try:
        controls = page.locator('button, [role="button"]').all()[:150]
    except Exception:  # noqa: BLE001
        return False
    for control in controls:
        try:
            if not control.is_visible(timeout=250) or not control.is_enabled(timeout=250):
                continue
            label = _mail_control_label(control)
            if not any(term in label for term in ("older", "next page", "next results", "plus anciens", "más antiguos", "mas antiguos", "ältere")):
                continue
            control.click(timeout=1500)
            return True
        except Exception:  # noqa: BLE001 - pagination can rerender between scans
            continue
    return False


def _mail_navigate_to_review_candidate(page: "Page", candidate: dict[str, str]) -> bool:
    """Open one matched thread by its Gmail route.

    Opening a message from a captured route is what keeps a queue alive: Gmail
    drops a message from a read-state filtered search the moment it is opened,
    so returning to the result list loses the remaining matches.
    """
    href = str(candidate.get("href") or "").strip()
    key = str(candidate.get("key") or "")
    thread_id = key.split(":", 1)[1] if key.startswith("gmail:") else ""
    target = ""
    if href:
        try:
            target = urljoin(_open_page_url(page) or "https://mail.google.com/", href)
        except ValueError:  # pragma: no cover - urljoin only rejects malformed input
            target = ""
    if not target and thread_id:
        target = f"https://mail.google.com/mail/u/0/#all/{thread_id}"
    if not target:
        return False
    parsed = urlparse(target)
    if parsed.scheme != "https" or (parsed.hostname or "").lower().rstrip(".") != "mail.google.com":
        return False
    try:
        page.goto(target, wait_until="commit", timeout=15000)
    except Exception:  # noqa: BLE001 - a hash-only change resolves without a load event
        pass
    if thread_id and thread_id in (_open_page_url(page) or ""):
        return True
    return not thread_id


def _mail_page_is_open(page) -> bool:
    try:
        is_closed = getattr(page, "is_closed", None)
        if callable(is_closed) and is_closed():
            return False
    except Exception:  # noqa: BLE001 - a closing page can raise on inspection
        return False
    url = _open_page_url(page)
    return bool(url) and not url.startswith(_BLANK_URL_PREFIXES)


def _mail_context_pages(page: "Page") -> list[object]:
    try:
        context = getattr(page, "context", None)
        return list(getattr(context, "pages", None) or [])
    except Exception:  # noqa: BLE001 - the context can be closing
        return []


def _mail_landing_host(page: "Page") -> str | None:
    """Return the confirmation page host, or None when it is not usable."""
    host = _mail_page_host(page)
    if host is None or host in _MAIL_CONFIRMATION_BLOCKED_HOSTS:
        return None
    return host


def _mail_page_host(page: "Page") -> str | None:
    url = _open_page_url(page)
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host:
        return None
    return host


def _mail_confirmation_landing_target(observer: "BrowserObserver", page: "Page", host: str) -> object | None:
    """Return the page opened by a clicked confirmation control, if any.

    Gmail opens a tracked message link in a new tab, so the confirmation page is
    looked up across the context instead of only on the mailbox tab. A sign-in
    page is returned as well so the message is reported for review instead of
    leaving the workflow waiting on a page that cannot be confirmed.
    """
    tracked = observer._mail_review_landing_page
    if tracked is not None:
        if _mail_page_is_open(tracked) and _mail_landing_host(tracked):
            return tracked
        observer._mail_review_landing_page = None
    if not observer._mail_review_awaiting_landing:
        return page if host and host not in _MAIL_CONFIRMATION_BLOCKED_HOSTS else None
    for candidate in _mail_context_pages(page):
        if candidate is page or candidate is tracked or not _mail_page_is_open(candidate):
            continue
        candidate_host = _mail_page_host(candidate)
        if candidate_host and candidate_host != "mail.google.com":
            return candidate
    if host and host != "mail.google.com":
        return page
    return None


def _mail_close_review_landing(observer: "BrowserObserver") -> None:
    page = observer._mail_review_landing_page
    observer._mail_review_landing_page = None
    observer._mail_review_landing_host = None
    if page is None or page is observer._mail_review_gmail_page:
        return
    try:
        page.close()
    except Exception:  # noqa: BLE001 - a detached confirmation tab is harmless
        logger.debug("could not close the confirmation tab for session %s", observer.session_id, exc_info=True)


def _mail_visible_text_terms(page: "Page", terms: tuple[str, ...]) -> tuple[str, ...]:
    try:
        text = " ".join(_visible_text(page).lower().split())
    except Exception:  # noqa: BLE001 - the page may be mid-navigation
        return ()
    return tuple(term for term in terms if term in text)


def _mail_click_landing_step(page: "Page") -> bool:
    """Advance one confirmation step on the page a message control opened.

    Multi-step confirmations keep their next control on the page Gmail routed
    to, so the same bounded label scan that activates the message control is
    repeated here for the remaining steps.
    """
    contexts = [page]
    try:
        contexts.extend(frame for frame in page.frames if frame is not page)
    except Exception:  # noqa: BLE001 - a frame can detach mid-scan
        pass
    best: tuple[int, str, object] | None = None
    best_rank: tuple[int, int, int] | None = None
    order = 0
    for context in contexts:
        try:
            controls = context.locator(_MAIL_CONFIRMATION_CONTROL_SELECTOR).all()[:_MAIL_CONFIRMATION_CONTROL_LIMITS[1]]
        except Exception:  # noqa: BLE001 - a cross-origin frame can refuse the query
            continue
        for control in controls:
            order += 1
            try:
                if not control.is_visible(timeout=300):
                    continue
                label = _mail_control_label(control)
            except Exception:  # noqa: BLE001
                continue
            if not label or _mail_confirmation_label_excluded(label):
                continue
            if len(label.split()) > _MAIL_LANDING_MAX_LABEL_WORDS:
                continue
            score = _mail_confirmation_score(label)
            if score < _MAIL_LANDING_CONFIRMATION_MIN_SCORE:
                continue
            kind = _mail_control_kind(control)
            rank = (score, 1 if kind == "button" else 0, -order)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best = (score, kind, control)
    if best is None:
        return False
    try:
        best[2].click(timeout=2500)
    except Exception:  # noqa: BLE001 - the control can re-render before the click
        return False
    return True


def _mail_fill_confirmation_details(observer: "BrowserObserver", page: "Page") -> tuple[bool, str, int]:
    """Fill publisher confirmation forms from this account's approved identity.

    The browser remains the authority for the live controls. A publisher
    password is available only for this newsletter's own confirmation session;
    verification codes, unknown required fields and unapproved choices stop
    the message for review rather than receiving guessed values.
    """
    config = observer.mail_workflow if observer.purpose == "mail" else observer.newsletter_config
    identity = config.get("_confirmation_identity") if observer.purpose == "mail" else config.get("test_identity")
    identity = identity if isinstance(identity, dict) else {}
    email = str((config.get("_confirmation_email") if observer.purpose == "mail" else config.get("account_email")) or "").strip()
    filled = 0
    unresolved: list[str] = []
    for surface in _mail_confirmation_surfaces(page):
        for control in _newsletter_live_controls(surface, include_name_step=True, include_confirmation_step=True):
            if control.get("hidden") or control.get("disabled"):
                continue
            field_key = semantic_field_key(control)
            required = control.get("required") is True
            state = _newsletter_live_control_state(control)
            input_type = str(control.get("type") or "").lower()
            label = _newsletter_live_label(control)
            agreement = agreement_checkbox_key(control)
            if agreement is not None:
                locator = _newsletter_live_locator(surface, control)
                if locator is None:
                    unresolved.append(label)
                    continue
                try:
                    if not locator.is_checked(timeout=500):
                        _newsletter_check_choice(locator)
                        filled += 1
                    if not locator.is_checked(timeout=700):
                        unresolved.append(label)
                except Exception:  # noqa: BLE001 - custom agreement controls vary
                    unresolved.append(label)
                continue
            if input_type in {"checkbox", "radio"}:
                if required and state == "empty":
                    unresolved.append(label)
                continue
            if field_key == "signup_password":
                value = str(config.get("signup_password") or "") if observer.purpose == "newsletter_confirm" else ""
                if not value:
                    unresolved.append("Publisher password required on confirmation page")
                    continue
                locator = _newsletter_live_locator(surface, control)
                if locator is None:
                    unresolved.append("Publisher password field could not be located")
                    continue
                try:
                    if str(control.get("value") or "") != value:
                        _newsletter_fill_text(locator, value)
                        filled += 1
                    if str(locator.input_value(timeout=700) or "") != value:
                        unresolved.append("Publisher password field did not retain its value")
                except Exception:  # noqa: BLE001 - never include secret values in details
                    unresolved.append("Publisher password field could not be filled")
                continue
            reason = blocked_live_control_reason(control)
            if reason is not None:
                if required or input_type == "password":
                    unresolved.append(f"{label} ({reason})")
                continue
            if field_key == "email":
                value = email
            elif field_key == "full_name":
                value = str(identity.get("full_name") or "").strip() or " ".join(
                    str(identity.get(key) or "").strip() for key in ("first_name", "last_name")
                ).strip()
            elif field_key in LIVE_FILL_KEYS and field_key != "custom_select":
                value = str(identity.get(field_key) or "").strip()
            else:
                if required and state == "empty":
                    unresolved.append(label)
                continue
            if not value:
                if required and state == "empty":
                    unresolved.append(label)
                continue
            if state == "set":
                if str(control.get("value") or "").strip().casefold() != value.casefold() and required:
                    unresolved.append(label)
                continue
            locator = _newsletter_live_locator(surface, control)
            if locator is None:
                if required:
                    unresolved.append(label)
                continue
            try:
                _newsletter_fill_text(locator, value)
                if str(locator.input_value(timeout=700) or "").strip().casefold() != value.casefold():
                    unresolved.append(label)
                else:
                    filled += 1
            except Exception:  # noqa: BLE001 - a publisher can rerender a form
                unresolved.append(label)
    if unresolved:
        return False, "Confirmation details need review: " + ", ".join(sorted(dict.fromkeys(unresolved))[:4]), filled
    return True, "", filled


def _mail_complete_review_queue(observer: "BrowserObserver") -> None:
    """Finish the matched-email run and report what was actually confirmed."""
    observer._mail_completed = True
    metadata = {
        "reviewed_message_count": observer._mail_review_processed_count,
        "candidate_confirmation_messages": observer._mail_review_candidate_count,
        "messages_without_rendered_controls": observer._mail_review_unrendered_count,
        "visible_message_controls": observer._mail_review_visible_control_count,
        "visible_message_buttons": observer._mail_review_button_count,
        "visible_message_text_links": observer._mail_review_text_link_count,
        "confirmation_links_followed": observer._mail_review_confirmation_steps,
        "matched_confirmation_emails": len(observer._mail_review_candidates),
        "confirmed_messages": observer._mail_review_confirmed_count,
        "messages_needing_review": observer._mail_review_attention_count,
    }
    if observer._mail_review_attention_count:
        # A run that opened matched emails but could not confirm every one of
        # them is reported as a failure with the counts, so no dashboard can
        # read a partial confirmation as a completed subscription queue.
        observer._emit(
            "mail.failed",
            (
                f"confirmed {observer._mail_review_confirmed_count} of {observer._mail_review_processed_count} opened confirmation email(s); "
                f"{observer._mail_review_attention_count} still need review"
            ),
            result="failure",
            metadata=metadata,
        )
        observer._stop.set()
        return
    if observer._mail_review_confirmed_count:
        observer._emit(
            "mail.completed",
            f"confirmed {observer._mail_review_confirmed_count} of {observer._mail_review_processed_count} opened confirmation email(s)",
            metadata=metadata,
        )
        observer._stop.set()
        return
    observer._emit(
        "mail.failed",
        (
            f"opened {observer._mail_review_processed_count} matching confirmation email(s) but none could be confirmed automatically; "
            f"{observer._mail_review_attention_count} need review"
        ),
        result="failure",
        metadata=metadata,
    )
    observer._stop.set()


def _mail_review_control_summary(page: "Page") -> dict[str, int] | None:
    """Count rendered confirmation controls without retaining their text or URLs.

    Gmail renders email HTML after the thread route changes. The review action
    waits for this scoped scan before it tells an employee that the message is
    ready. It intentionally reports aggregate counts only and never clicks a
    control or reads a destination URL.
    """
    try:
        summary = page.evaluate(
            """confirmationCues => {
                const roots = [...new Set([
                    ...document.querySelectorAll('[role="main"] .a3s'),
                    ...document.querySelectorAll('[role="main"] .adn.ads'),
                    ...document.querySelectorAll('[role="main"] [data-message-id] .ii.gt'),
                ])];
                if (!roots.length) return null;
                const visible = node => {
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                };
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim().toLocaleLowerCase();
                const controls = [...new Set(roots.flatMap(root => [...root.querySelectorAll(
                    'a[href], [role="link"], button, input[type="button"], input[type="submit"], [role="button"]'
                )]))];
                let controlCount = 0;
                let confirmationCount = 0;
                let buttonCount = 0;
                let textLinkCount = 0;
                for (const control of controls) {
                    if (!visible(control)) continue;
                    const tag = control.tagName.toLowerCase();
                    const isTextLink = tag === 'a' || control.getAttribute('role') === 'link';
                    const isButton = !isTextLink;
                    const label = normalize([
                        control.innerText,
                        control.textContent,
                        control.value,
                        control.getAttribute('aria-label'),
                        control.getAttribute('title'),
                    ].filter(Boolean).join(' '));
                    // A rendered control is recorded even when it has no
                    // recognizable confirmation wording, so employees can
                    // review every visible message action themselves.
                    controlCount += 1;
                    if (isTextLink) textLinkCount += 1;
                    if (isButton) buttonCount += 1;
                    if (confirmationCues.some(cue => label.includes(cue))) confirmationCount += 1;
                }
                return { controlCount, confirmationCount, buttonCount, textLinkCount };
            }""",
            list(_MAIL_CONFIRMATION_CUE_TERMS),
        )
    except Exception:  # noqa: BLE001 - Gmail can still be mounting the opened message
        return None
    if not isinstance(summary, dict):
        return None
    try:
        return {
            "control_count": max(0, int(summary.get("controlCount", 0))),
            "confirmation_count": max(0, int(summary.get("confirmationCount", 0))),
            "button_count": max(0, int(summary.get("buttonCount", 0))),
            "text_link_count": max(0, int(summary.get("textLinkCount", 0))),
        }
    except (TypeError, ValueError):
        return None


def _mail_matching_row_count(page: "Page", config: dict[str, object]) -> int:
    """Count visible subject matches after a toolbar action.

    Gmail normally removes a moved message from the current Spam/Promotions
    result. This verification prevents a successful-looking session when a
    toolbar click was ignored or a stale result list remained on screen.
    """
    try:
        return int(page.evaluate(
            """config => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const decodeSubject = value => {
                    try { return decodeURIComponent(value || ''); } catch (_) { return value || ''; }
                };
                const subjects = [...new Set([config.subject, decodeSubject(config.subject)])]
                    .map(normalize)
                    .filter(Boolean);
                const exact = config.match_mode === 'exact';
                const rows = [...new Set([
                    ...document.querySelectorAll('[role="main"] tr.zA'),
                    ...document.querySelectorAll('[role="main"] tr'),
                    ...document.querySelectorAll('[role="main"] [role="row"]'),
                    ...document.querySelectorAll('[role="grid"] [role="row"]'),
                    ...document.querySelectorAll('tr.zA'),
                    ...document.querySelectorAll('.zA')
                ])];
                const visible = node => {
                    const rect = node.getBoundingClientRect?.();
                    const style = getComputedStyle(node);
                    return !!rect && rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                return rows.filter(row => {
                    if (!visible(row)) return false;
                    const text = normalize([row.innerText || '', row.getAttribute('aria-label') || ''].join(' '));
                    const lines = (row.innerText || '').split(/\\n+/).map(normalize);
                    return exact
                        ? subjects.some(subject => lines.some(line => line === subject))
                        : subjects.some(subject => text.includes(subject));
                }).length;
            }""",
            {"subject": unquote(str(config.get("subject") or "")), "match_mode": config.get("match_mode", "contains")},
        ))
    except Exception:  # noqa: BLE001 - Gmail can replace the result list while moving
        return 0


def _mail_control_label(control) -> str:
    """Read Gmail's accessible label across its changing control variants."""
    values: list[str] = []
    for attribute in ("aria-label", "title", "data-tooltip", "data-tooltip-text", "data-title", "name", "value"):
        try:
            value = control.get_attribute(attribute)
        except Exception:  # noqa: BLE001 - a control can detach between reads
            value = None
        if value:
            values.append(str(value))
    try:
        text = control.inner_text()
    except Exception:  # noqa: BLE001 - a control can detach between reads
        text = ""
    if text:
        values.insert(0, str(text))
    normalized = " ".join(" ".join(values).replace("\u00a0", " ").split())
    return unicodedata.normalize("NFKC", normalized).casefold()


def _mail_label_matches(label: str, labels: tuple[str, ...]) -> bool:
    normalized = " ".join(label.replace("\u00a0", " ").split()).casefold()
    for term in labels:
        candidate = " ".join(term.replace("\u00a0", " ").split()).casefold()
        if normalized == candidate or normalized.startswith(candidate + " ") or normalized.endswith(" " + candidate) or f" {candidate} " in normalized:
            return True
    return False


def _mail_click_action(page: "Page", action: str) -> bool:
    labels = _MAIL_ACTION_LABELS.get(action, ())
    try:
        candidates = page.locator(_MAIL_CONTROL_SELECTOR)
        for candidate in candidates.all():
            try:
                if not candidate.is_visible(timeout=300):
                    continue
                label = _mail_control_label(candidate)
            except Exception:  # noqa: BLE001
                continue
            if _mail_label_matches(label, labels):
                try:
                    candidate.click(timeout=1500)
                    return True
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001 - Gmail may replace the toolbar after selection
        return False
    return False


def _mail_reply_surface_ready(page: "Page") -> bool:
    """Return whether a localized Reply control is visible in the opened thread.

    Opening a Gmail result row is asynchronous.  The inbox row can remain in
    the DOM for several observer ticks while the thread view mounts, so Reply
    must wait for its own control instead of re-running inbox selection.
    """
    labels = _MAIL_ACTION_LABELS["reply"]
    try:
        controls = page.locator(_MAIL_CONTROL_SELECTOR)
        for control in controls.all():
            try:
                if not control.is_visible(timeout=300):
                    continue
                label = _mail_control_label(control)
            except Exception:  # noqa: BLE001 - Gmail can replace controls while mounting
                continue
            if _mail_label_matches(label, labels):
                return True
    except Exception:  # noqa: BLE001 - the thread may still be navigating
        return False
    return False


def _mail_reply_editor(page: "Page"):
    """Find Gmail's visible reply editor across its editor variants."""
    selectors = (
        '[contenteditable="true"][role="textbox"]',
        '[contenteditable="true"][aria-label*="message" i]',
        '[contenteditable="true"]',
        'textarea[name="body"]',
    )
    for selector in selectors:
        try:
            for editor in page.locator(selector).all():
                if editor.is_visible(timeout=300):
                    return editor
        except Exception:  # noqa: BLE001 - Gmail can replace the editor during open
            continue
    return None


def _mail_fill_reply(page: "Page", body: str) -> bool:
    expected = str(body or "")
    if not expected.strip():
        return False
    expected_normalized = " ".join(expected.split())
    for _attempt in range(2):
        editor = _mail_reply_editor(page)
        if editor is None:
            return False
        try:
            editor.fill(expected, timeout=1500)
            try:
                actual = " ".join((editor.inner_text(timeout=800) or "").split())
            except Exception:  # noqa: BLE001 - some editor variants reject inner_text during rerender
                actual = ""
            if expected_normalized in actual or actual == expected_normalized:
                return True
            # Gmail occasionally remounts the contenteditable after fill().
            # Reacquire it and use normal trusted keyboard input once rather
            # than leaving a visually filled but unsent draft.
            editor.click(timeout=1200)
            editor.press("ControlOrMeta+A", timeout=1200)
            editor.press("Backspace", timeout=1200)
            editor.type(expected, delay=5, timeout=5000)
            actual = " ".join((editor.inner_text(timeout=800) or "").split())
            if expected_normalized in actual or actual == expected_normalized:
                return True
        except Exception:  # noqa: BLE001 - Gmail may re-render the compose surface
            continue
    return False


def _mail_click_reply_send(page: "Page") -> bool:
    try:
        controls = page.locator(_MAIL_CONTROL_SELECTOR)
        for control in controls.all():
            try:
                if not control.is_visible(timeout=300):
                    continue
                if control.get_attribute("aria-disabled") == "true" or control.get_attribute("disabled") is not None:
                    continue
                label = _mail_control_label(control)
                if _mail_label_matches(label, _MAIL_REPLY_SEND_LABELS):
                    control.click(timeout=1500)
                    return True
            except Exception:  # noqa: BLE001 - continue scanning localized controls
                continue
    except Exception:  # noqa: BLE001 - Gmail may re-render the reply surface
        return False
    return False


_MAIL_SPAM_CONFIRM_LABELS = (
    "report spam", "signaler comme spam", "signaler comme indésirable",
    "signaler comme indesirable", "denunciar spam", "reportar spam",
    "marcar como spam",
)


def _mail_confirm_spam_dialog(page: "Page") -> bool:
    """Confirm Gmail's Report spam/Unsubscribe choice without unsubscribing."""
    try:
        dialogs = page.locator('[role="dialog"], [aria-modal="true"]')
        for dialog in dialogs.all():
            try:
                if not dialog.is_visible(timeout=300):
                    continue
                dialog_text = " ".join(filter(None, [dialog.inner_text(), dialog.get_attribute("aria-label")])).lower()
                if not any(term in dialog_text for term in ("unsubscribe", "se désabonner", "se desabonner", "darse de baja", "spam")):
                    continue
                buttons = dialog.locator('button, [role="button"], input[type="button"], input[type="submit"]')
                for button in buttons.all():
                    try:
                        if not button.is_visible(timeout=300):
                            continue
                        label = " ".join(filter(None, [button.inner_text(), button.get_attribute("aria-label"), button.get_attribute("title"), button.get_attribute("data-tooltip")])).strip().lower()
                        if any(term in label for term in _MAIL_SPAM_CONFIRM_LABELS) and "unsubscribe" not in label and "désabonner" not in label and "desabonner" not in label:
                            button.click(timeout=1500)
                            return True
                    except Exception:  # noqa: BLE001 - dialog controls can re-render after click
                        continue
            except Exception:  # noqa: BLE001 - Gmail may replace the dialog node
                continue
    except Exception:  # noqa: BLE001 - no confirmation dialog is also a valid Gmail flow
        return False
    return False


def _mail_action_confirmation_visible(page: "Page", action: str) -> bool:
    """Recognize Gmail's localized toast/status after a move."""
    text = _visible_text(page).lower()
    if action == "reply":
        return any(term in text for term in _MAIL_REPLY_SUCCESS_TERMS)
    if action == "spam_to_inbox":
        terms = (
            "marked as not spam", "not spam", "no es spam", "pas du spam",
            "pas un spam", "marqué comme non spam", "marque comme non spam",
            "marcado como no spam", "moved to inbox", "move to inbox",
            "déplacé vers la boîte de réception", "deplace vers la boite de reception",
        )
    else:
        terms = (
            "reported as spam", "report as spam", "denunciado como spam",
            "signaler comme indésirable", "signale comme indesirable",
            "reportado como spam", "marcado como spam",
        )
    return any(term in text for term in terms)


def _handle_mail_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    """Find subject-matched messages and perform one bounded Gmail action.

    Search scope is encoded in Gmail's own query (Spam or Promotions), while
    the move is done through the visible Gmail toolbar. The observer never
    touches message bodies and stops after one verified button click, so a
    re-render cannot repeat the action.
    """
    config = observer.mail_workflow
    if config.get("action") == "confirmation_review":
        # Confirmation emails leave Gmail for the confirmation page their own
        # control opens, so this action owns its host routing end to end.
        _handle_mail_confirmation_tick(observer, page, host)
        return
    if host != "mail.google.com":
        return
    observer._mail_tick_count += 1
    if observer._mail_tick_count > _MAIL_MAX_TICKS:
        observer._emit("mail.failed", "Gmail did not finish the subject workflow before the safe timeout", result="failure")
        observer._stop.set()
        return
    if not observer._mail_search_submitted:
        if _mail_submit_search(observer, page):
            observer._mail_search_submitted = True
            observer._mail_search_wait_ticks = 0
            observer._emit("mail.search_submitted", "searched Gmail for the requested subject line")
        else:
            observer._mail_search_wait_ticks += 1
            if observer._mail_search_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS:
                observer._emit("mail.failed", "Gmail search was not available in the authenticated mailbox", result="failure")
                observer._stop.set()
        return
    if not observer._mail_selection_done:
        observer._mail_search_wait_ticks += 1
        matches = _mail_select_subject_rows(page, config)
        if matches:
            if config.get("action") == "reply":
                # Finding a row is not enough for Reply. Open the
                # thread with
                # a trusted locator click so Gmail changes from search results
                # to the message view before any Reply control is scanned.
                if _mail_open_reply_thread(page, config):
                    observer._mail_selection_done = True
                    observer._mail_match_count = 1
                    observer._mail_reply_thread_opened = True
                    observer._mail_action_wait_ticks = 0
                    observer._emit("mail.matches_found", "found 1 subject-matched message")
                    observer._emit("mail.thread_opened", "opened the matching Gmail thread")
                else:
                    observer._mail_action_wait_ticks += 1
                    if observer._mail_action_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS:
                        observer._mail_selection_attempts += 1
                        observer._mail_action_wait_ticks = 0
                        if observer._mail_selection_attempts <= 3:
                            observer._mail_search_wait_ticks = 0
                            observer._emit("mail.selection_retry", "the matching row was found but its subject link was not ready; retrying the thread open")
                        else:
                            observer._emit("mail.failed", "Gmail found the matching message but could not open its thread", result="failure")
                            observer._stop.set()
                return
            observer._mail_selection_done = True
            observer._mail_match_count = matches
            observer._emit("mail.matches_found", f"found {matches} subject-matched message(s)")
        elif observer._mail_search_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS:
            # Search results can render their rows after the URL changes, and
            # Gmail sometimes replaces the result table after the first scan.
            # Retry the bounded scan before declaring that there were no
            # matches; completion without a match was the previous false
            # success reported by the user.
            observer._mail_selection_attempts += 1
            if observer._mail_selection_attempts <= 3:
                observer._mail_search_wait_ticks = 0
                observer._emit("mail.selection_retry", "Gmail results were present but no selectable matching row was ready; rescanning")
            else:
                observer._emit("mail.failed", "Gmail search completed but no selectable message matched the subject line", result="failure")
                observer._stop.set()
        return
    if config.get("action") == "reply" and not observer._mail_action_clicked:
        # The opened thread renders asynchronously. Scroll its message pane
        # once before scanning for the localized Reply control.
        if not observer._mail_reply_scrolled:
            if _mail_scroll_reply_thread(page):
                observer._mail_reply_scrolled = True
                observer._mail_action_wait_ticks = 0
                observer._emit("mail.thread_scrolled", "prepared the opened Gmail thread to expose the reply controls")
            else:
                observer._mail_action_wait_ticks += 1
                if observer._mail_action_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS:
                    observer._emit("mail.failed", "The opened Gmail thread did not become scrollable", result="failure")
                    observer._stop.set()
            return
        if _mail_reply_surface_ready(page) and _mail_click_action(page, "reply"):
            observer._mail_action_clicked = True
            observer._mail_action_wait_ticks = 0
            observer._mail_verification_wait_ticks = 0
            observer._emit("mail.reply_opened", "opened the matching Gmail thread and selected its localized Reply control")
        else:
            observer._mail_action_wait_ticks += 1
            if observer._mail_action_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS:
                observer._emit("mail.failed", "The matching Gmail thread did not expose a Reply control", result="failure")
                observer._stop.set()
        return
    if not observer._mail_action_clicked:
        if _mail_click_action(page, str(config.get("action") or "")):
            observer._mail_action_clicked = True
            observer._mail_action_wait_ticks = 0
            observer._mail_verification_wait_ticks = 0
            observer._emit("mail.action_clicked", "applied the selected Gmail move to the matched messages")
        else:
            observer._mail_action_wait_ticks += 1
            # The selection toolbar is re-rendered after a row click. If the
            # first toolbar scan misses it, re-select the same rows instead of
            # silently ending the workflow.
            if observer._mail_action_wait_ticks >= 4:
                if observer._mail_selection_attempts < 3:
                    observer._mail_selection_done = False
                    observer._mail_action_wait_ticks = 0
                    observer._mail_search_wait_ticks = 0
                    observer._mail_selection_attempts += 1
                    observer._emit("mail.selection_retry", "Gmail replaced the selection toolbar; retrying the matching row selection")
                else:
                    observer._emit("mail.failed", "The selected Gmail move was not available after matching messages", result="failure")
                    observer._stop.set()
        return
    if config.get("action") == "reply":
        if not observer._mail_reply_typed:
            if _mail_fill_reply(page, str(config.get("reply_body") or "")):
                observer._mail_reply_typed = True
                observer._mail_action_wait_ticks = 0
                observer._emit("mail.reply_typed", "entered the prepared reply in the localized Gmail composer")
            else:
                observer._mail_action_wait_ticks += 1
                if observer._mail_action_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS:
                    observer._emit("mail.failed", "The Gmail reply editor did not become available", result="failure")
                    observer._stop.set()
            return
        if not observer._mail_reply_send_clicked:
            if _mail_click_reply_send(page):
                observer._mail_reply_send_clicked = True
                observer._mail_verification_wait_ticks = 0
                observer._emit("mail.reply_send_clicked", "clicked Gmail Send; waiting for the localized sent confirmation")
            else:
                observer._mail_action_wait_ticks += 1
                if observer._mail_action_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS:
                    observer._emit("mail.failed", "The Gmail reply could not be sent", result="failure")
                    observer._stop.set()
            return
    if config.get("action") == "promotions_to_spam" and not observer._mail_followup_confirmed:
        if _mail_confirm_spam_dialog(page):
            observer._mail_followup_confirmed = True
            observer._mail_verification_wait_ticks = 0
            observer._emit("mail.confirmation_clicked", "confirmed Report spam in Gmail's follow-up dialog")
            return
    observer._mail_action_wait_ticks += 1
    observer._mail_verification_wait_ticks += 1
    action = str(config.get("action") or "")
    remaining = _mail_matching_row_count(page, config)
    # A reply opens the thread view, where the search row is normally no
    # longer present. An empty row count is therefore not evidence that Gmail
    # delivered the reply; require the explicit localized sent confirmation.
    if action == "reply" and _mail_action_confirmation_visible(page, action):
        observer._mail_reply_confirmation_seen = True
    confirmed = (
        observer._mail_reply_confirmation_seen
        if action == "reply"
        else remaining == 0 or _mail_action_confirmation_visible(page, action)
    )
    if confirmed and observer._mail_verification_wait_ticks >= _MAIL_ACTION_SETTLE_TICKS:
        observer._mail_completed = True
        observer._emit("mail.completed", f"completed the selected Gmail move for {observer._mail_match_count} matched message(s)")
        observer._stop.set()
    elif observer._mail_verification_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS:
        detail = (
            "Gmail did not show a localized sent confirmation after Send was clicked"
            if action == "reply"
            else f"Gmail did not verify the move; {remaining} matching message(s) remain visible"
        )
        observer._emit("mail.failed", detail, result="failure")
        observer._stop.set()


def _mail_finish_review_message(observer: "BrowserObserver", config: dict[str, object], outcome: str, detail: str, event: str, result: str, metadata: dict[str, object] | None = None) -> None:
    """Record one matched email's outcome and move to the next one.

    A single-email run ends here: a confirmed message completes the workflow,
    while anything that could not be confirmed is left open in the browser for
    the assigned employee instead of reporting a false success.
    """
    observer._mail_review_processed_count += 1
    confirmed = outcome == "confirmed"
    # No further page is read for this message. A confirmation control that
    # navigated the mailbox tab away from Gmail leaves that page behind, and it
    # must not be read again as if it still belonged to an open message.
    observer._mail_review_in_flight = False
    if confirmed:
        observer._mail_review_confirmed_count += 1
    else:
        observer._mail_review_attention_count += 1
    observer._emit(event, detail, result=result, metadata={**(metadata or {}), "message_outcome": outcome})
    _mail_close_review_landing(observer)
    observer._mail_selection_done = False
    observer._mail_reply_thread_opened = False
    observer._mail_review_control_clicked = False
    observer._mail_review_awaiting_landing = False
    observer._mail_review_current_key = None
    observer._mail_review_landing_steps = 0
    observer._mail_review_landing_idle_ticks = 0
    observer._mail_review_scan_ticks = 0
    observer._mail_review_stable_ticks = 0
    observer._mail_review_scanned = False
    observer._mail_review_last_summary = None
    observer._mail_action_wait_ticks = 0
    observer._mail_search_wait_ticks = 0
    if _mail_review_queue_mode(config):
        return
    if confirmed:
        _mail_complete_review_queue(observer)
        return
    observer._mail_review_ready = True
    observer._emit(
        "mail.review_ready",
        f"{detail}; the matched confirmation email stays open for employee review",
        result="attention",
        metadata={
            **(metadata or {}),
            "confirmed_messages": observer._mail_review_confirmed_count,
            "messages_needing_review": observer._mail_review_attention_count,
        },
    )


def _mail_collect_review_candidates(observer: "BrowserObserver", page: "Page", config: dict[str, object]) -> None:
    """Locate every matched thread Gmail still has rendered for the query.

    The candidates are captured once. Re-running the query between messages
    cannot be used because Gmail moves an opened message out of a read-state
    filtered result, which is what emptied the previous queue.

    The collection only ends on a settled result list: either Gmail reports the
    query matched nothing, the selected number of messages was captured, or
    the list has produced no new match for several scans and cannot be scrolled
    any further. An empty mailbox is never concluded from a list that has not
    rendered yet.

    Gmail is also allowed to finish leaving the mailbox it was on. Pressing Enter
    in the search box does not change the route immediately, and reading the
    still-visible inbox rows is what made a previous run report an empty mailbox
    seconds after a successful search.
    """
    limit = _mail_review_queue_limit(config)
    applied, route = _mail_search_applied(page, config)
    if not applied:
        observer._mail_review_route_wait_ticks += 1
        if observer._mail_review_route_wait_ticks < _MAIL_REVIEW_ROUTE_WAIT_TICKS:
            return
        observer._mail_review_collection_done = True
        observer._emit(
            "mail.failed",
            "Gmail did not open a result list for the requested confirmation subject",
            result="failure",
            metadata={"matched_confirmation_emails": 0, "search_route": route},
        )
        observer._stop.set()
        return
    observer._mail_review_route_wait_ticks = 0
    observer._mail_review_collection_ticks += 1
    room = max(0, limit - len(observer._mail_review_candidates))
    fresh = [
        candidate
        for candidate in _mail_review_candidate_rows(page, config)
        if candidate["key"] not in observer._mail_review_candidate_keys
    ][:room]
    for candidate in fresh:
        observer._mail_review_candidates.append(candidate)
        observer._mail_review_candidate_keys.add(candidate["key"])
    if fresh:
        observer._mail_review_collection_stall_ticks = 0
    else:
        observer._mail_review_collection_stall_ticks += 1
    status = _mail_search_list_status(page)
    observer._mail_review_visible_rows = max(observer._mail_review_visible_rows, int(status["visible_rows"]))
    collected = len(observer._mail_review_candidates)
    # Gmail renders "No messages matched your search" while it is still
    # replacing the previous list, so the empty state has to hold for more than
    # one scan before the mailbox can be reported as holding no match.
    if status["no_results"] and not status["visible_rows"]:
        observer._mail_review_empty_streak += 1
    else:
        observer._mail_review_empty_streak = 0
    settled = observer._mail_review_collection_stall_ticks >= _MAIL_REVIEW_COLLECTION_STALL_TICKS
    empty = observer._mail_review_empty_streak >= _MAIL_REVIEW_EMPTY_SETTLE_TICKS
    if collected < limit and not empty and settled and observer._mail_review_collection_ticks >= _MAIL_REVIEW_MIN_COLLECTION_TICKS:
        if observer._mail_review_scrolls < _MAIL_REVIEW_MAX_SCROLLS and _mail_review_scroll_results(page):
            observer._mail_review_scrolls += 1
            observer._mail_review_collection_stall_ticks = 0
            return
        if observer._mail_review_pages_advanced < _MAIL_REVIEW_MAX_PAGES and _mail_review_next_page(page):
            observer._mail_review_pages_advanced += 1
            observer._mail_review_scrolls = 0
            observer._mail_review_collection_stall_ticks = 0
            return
    finished = collected >= limit or empty or (settled and observer._mail_review_collection_ticks >= _MAIL_REVIEW_MIN_COLLECTION_TICKS)
    if not finished:
        return
    observer._mail_review_collection_done = True
    observer._emit(
        "mail.candidates_collected",
        f"located {collected} matching confirmation email(s) in this mailbox",
        metadata={
            "matched_confirmation_emails": collected,
            "per_account_limit": limit,
            "search_list_rows": observer._mail_review_visible_rows,
            "search_route": route,
            "result_list_settled": collected >= limit or empty,
            "search_pages_scanned": observer._mail_review_pages_advanced + 1,
        },
    )
    if collected:
        return
    observer._mail_review_empty_reason = "no_results" if empty else "unmatched"
    observer._emit(
        "mail.failed",
        (
            "the Gmail search matched no email at all"
            if empty
            else f"the Gmail search rendered {observer._mail_review_visible_rows} row(s) but none matched the requested subject"
        ),
        result="failure",
        metadata={"matched_confirmation_emails": 0, "search_list_rows": observer._mail_review_visible_rows, "search_route": route},
    )
    observer._stop.set()


def _mail_open_next_review_candidate(observer: "BrowserObserver", page: "Page", config: dict[str, object]) -> None:
    """Open the next captured match and announce it."""
    index = observer._mail_review_processed_count
    limit = _mail_review_queue_limit(config)
    if index >= len(observer._mail_review_candidates):
        _mail_complete_review_queue(observer)
        return
    candidate = observer._mail_review_candidates[index]
    opened = _mail_navigate_to_review_candidate(page, candidate)
    if not opened:
        # A row without a usable Gmail route is still opened with a trusted
        # subject click, exactly like the single-message Reply surface.
        opened = bool(_mail_open_next_review_thread(page, config, observer._mail_review_opened_thread_ids))
        if opened:
            candidate = {"key": f"visible:{index}", "href": ""}
            observer._mail_review_candidates[index] = candidate
    if not opened:
        observer._mail_action_wait_ticks += 1
        if observer._mail_action_wait_ticks < _MAIL_CONFIRMATION_CONTROL_WAIT_TICKS:
            return
        observer._mail_review_unrendered_count += 1
        _mail_finish_review_message(
            observer,
            config,
            "unavailable",
            f"matched confirmation email {index + 1} of {limit} could not be opened in Gmail",
            "mail.review_message_unavailable",
            "attention",
            {"message_controls_rendered": False, "reviewed_message_count": index + 1},
        )
        return
    observer._mail_action_wait_ticks = 0
    observer._mail_search_wait_ticks = 0
    observer._mail_selection_done = True
    observer._mail_match_count = index + 1
    observer._mail_reply_thread_opened = True
    observer._mail_review_opened_thread_ids.add(candidate["key"])
    observer._mail_review_current_key = candidate["key"]
    observer._mail_review_scan_ticks = 0
    observer._mail_review_stable_ticks = 0
    observer._mail_review_scanned = False
    observer._mail_review_last_summary = None
    observer._mail_review_control_clicked = False
    observer._mail_review_landing_steps = 0
    observer._mail_review_landing_idle_ticks = 0
    observer._mail_review_gmail_page = page
    observer._mail_review_in_flight = True
    observer._emit(
        "mail.matches_found",
        f"opened confirmation email {index + 1} of {limit}",
        metadata={"reviewed_message_count": index + 1, "matched_confirmation_emails": len(observer._mail_review_candidates)},
    )
    observer._emit("mail.thread_opened", "opened the next matching Gmail thread")


def _mail_handle_reviewed_message(observer: "BrowserObserver", page: "Page", config: dict[str, object]) -> None:
    """Scan one opened message and activate its rendered confirmation control."""
    observer._mail_review_scan_ticks += 1
    summary = _mail_review_control_summary(page)
    if summary is None:
        if observer._mail_review_scan_ticks < _MAIL_SEARCH_WAIT_TICKS:
            return
        observer._mail_review_unrendered_count += 1
        _mail_finish_review_message(
            observer,
            config,
            "unrendered",
            "opened a matching confirmation email but Gmail did not render its message controls",
            "mail.review_message_unavailable",
            "attention",
            {"message_controls_rendered": False, "reviewed_message_count": observer._mail_review_processed_count + 1},
        )
        return
    signature = (
        summary["control_count"],
        summary["confirmation_count"],
        summary["button_count"],
        summary["text_link_count"],
    )
    if signature == observer._mail_review_last_summary:
        observer._mail_review_stable_ticks += 1
    else:
        observer._mail_review_last_summary = signature
        observer._mail_review_stable_ticks = 0
    # Two matching scans prevent an animated email body from being acted on
    # before all of its controls have appeared.
    if observer._mail_review_stable_ticks < 1 and observer._mail_review_scan_ticks < 5:
        return
    position = observer._mail_review_processed_count + 1
    metadata = {
        "message_controls_rendered": True,
        "reviewed_message_count": position,
        "visible_message_controls": summary["control_count"],
        "candidate_confirmation_controls": summary["confirmation_count"],
        "visible_message_buttons": summary["button_count"],
        "visible_message_text_links": summary["text_link_count"],
    }
    if not observer._mail_review_scanned:
        # One scan per message is reported and counted, so a control that takes
        # a few ticks to become clickable cannot inflate the run totals.
        observer._mail_review_scanned = True
        observer._mail_review_visible_control_count += summary["control_count"]
        observer._mail_review_button_count += summary["button_count"]
        observer._mail_review_text_link_count += summary["text_link_count"]
        if summary["confirmation_count"]:
            observer._mail_review_candidate_count += 1
            observer._emit(
                "mail.review_controls_detected",
                f"detected {summary['confirmation_count']} confirmation control(s) among {summary['control_count']} visible message controls",
                result="attention",
                metadata=metadata,
            )
        else:
            observer._emit(
                "mail.review_message_scanned",
                f"scanned confirmation email {position}; no recognizable confirmation control was visible",
                metadata=metadata,
            )
    control_kind = _mail_click_confirmation_control(page)
    if control_kind is None:
        observer._mail_action_wait_ticks += 1
        if observer._mail_action_wait_ticks < _MAIL_CONFIRMATION_CONTROL_WAIT_TICKS:
            return
        detected = summary["confirmation_count"] > 0
        _mail_finish_review_message(
            observer,
            config,
            "no_control",
            (
                f"confirmation email {position} exposed no confirmation button or link that could be activated"
                if not detected
                else f"the confirmation control rendered in email {position} could not be activated"
            ),
            "mail.confirmation_control_missing" if not detected else "mail.confirmation_control_unavailable",
            "attention",
            {**metadata, "message_index": position},
        )
        return
    observer._mail_action_wait_ticks = 0
    observer._mail_review_control_clicked = True
    observer._mail_review_awaiting_landing = True
    observer._mail_review_landing_idle_ticks = 0
    observer._mail_review_confirmation_steps += 1
    observer._emit(
        "mail.confirmation_clicked",
        f"activated the {control_kind.replace('_', ' ')} in confirmation email {position}",
        metadata={"message_index": position, "control_kind": control_kind, "confirmation_links_followed": observer._mail_review_confirmation_steps},
    )


def _mail_handle_confirmation_page(observer: "BrowserObserver", page: "Page", config: dict[str, object]) -> None:
    """Drive the confirmation page a message control opened to a decision."""
    observer._mail_review_awaiting_landing = False
    host = _mail_landing_host(page)
    position = observer._mail_review_processed_count + 1
    if host is None:
        _mail_finish_review_message(
            observer,
            config,
            "blocked",
            f"the confirmation control in email {position} opened a sign-in page instead of a confirmation page",
            "mail.confirmation_message_review",
            "attention",
            {"message_index": position},
        )
        return
    if observer._mail_review_landing_page is None:
        observer._mail_review_landing_page = page
        observer._mail_review_landing_host = host
        observer._mail_review_landing_idle_ticks = 0
        observer._emit(
            "mail.confirmation_page_opened",
            f"opened the confirmation page for email {position} on {host}",
            metadata={"message_index": position, "page_host": host},
        )
        return
    if _mail_visible_text_terms(page, _MAIL_CONFIRMATION_FAILURE_TERMS):
        _mail_finish_review_message(
            observer,
            config,
            "failed",
            f"the confirmation page for email {position} reported that the request was not completed",
            "mail.confirmation_failed",
            "attention",
            {"message_index": position, "page_host": host, "steps_taken": observer._mail_review_landing_steps},
        )
        return
    if _mail_visible_text_terms(page, _MAIL_CONFIRMATION_SUCCESS_TERMS):
        _mail_finish_review_message(
            observer,
            config,
            "confirmed",
            f"confirmed confirmation email {position}",
            "mail.confirmation_succeeded",
            "success",
            {"message_index": position, "page_host": host, "steps_taken": observer._mail_review_landing_steps},
        )
        return
    ready, detail, filled = _mail_fill_confirmation_details(observer, page)
    if not ready:
        _mail_finish_review_message(
            observer, config, "details_unavailable",
            f"confirmation email {position} needs operator review: {detail}",
            "mail.confirmation_message_review", "attention",
            {"message_index": position, "page_host": host, "steps_taken": observer._mail_review_landing_steps},
        )
        return
    if filled:
        observer._emit("mail.confirmation_details_filled", f"filled {filled} approved confirmation field(s) for email {position}")
    if _mail_click_landing_step(page):
        observer._mail_review_landing_steps += 1
        observer._mail_review_confirmation_steps += 1
        observer._mail_review_landing_idle_ticks = 0
        observer._emit(
            "mail.confirmation_step_completed",
            f"selected confirmation step {observer._mail_review_landing_steps} of {_MAIL_CONFIRMATION_MAX_STEPS} for email {position}",
            metadata={
                "message_index": position,
                "page_host": host,
                "step": observer._mail_review_landing_steps,
                "confirmation_links_followed": observer._mail_review_confirmation_steps,
            },
        )
        return
    observer._mail_review_landing_idle_ticks += 1
    if observer._mail_review_landing_idle_ticks < _MAIL_CONFIRMATION_LANDING_SETTLE_TICKS:
        return
    _mail_finish_review_message(
        observer,
        config,
        "unverified",
        f"the confirmation page for email {position} stopped after {observer._mail_review_landing_steps} automated step(s) without reporting a confirmed result",
        "mail.confirmation_message_review",
        "attention",
        {"message_index": position, "page_host": host, "steps_taken": observer._mail_review_landing_steps},
    )


def _handle_mail_confirmation_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    """Locate the matched confirmation emails and confirm them one at a time.

    The mailbox is treated as a bounded queue: every matched thread is located
    once, opened one by one, and its rendered confirmation button or clickable
    text is activated with a trusted click. Whatever page that control opens is
    then driven through its own remaining confirmation steps before the next
    matched email is opened, so a multi-step confirmation is completed instead
    of abandoned at the first page.
    """
    if observer._mail_review_ready:
        # One matched message is open for the assigned employee. Keep the
        # browser as it is instead of closing it or activating more controls.
        return
    config = observer.mail_workflow
    queue_mode = _mail_review_queue_mode(config)
    observer._mail_tick_count += 1
    if observer._mail_tick_count > _mail_confirmation_tick_budget(config):
        observer._emit(
            "mail.failed",
            f"Gmail did not confirm the selected confirmation emails before the safe timeout ({observer._mail_review_confirmed_count} confirmed so far)",
            result="failure",
            metadata={"confirmed_messages": observer._mail_review_confirmed_count, "reviewed_message_count": observer._mail_review_processed_count},
        )
        observer._stop.set()
        return
    landing = _mail_confirmation_landing_target(observer, page, host) if observer._mail_review_in_flight else None
    if landing is not None:
        _mail_handle_confirmation_page(observer, landing, config)
        return
    if observer._mail_review_awaiting_landing:
        # The confirmation control was activated; its page has not rendered yet.
        observer._mail_review_landing_idle_ticks += 1
        if observer._mail_review_landing_idle_ticks >= _MAIL_CONFIRMATION_LANDING_WAIT_TICKS:
            _mail_finish_review_message(
                observer,
                config,
                "no_page",
                f"the confirmation control in email {observer._mail_review_processed_count + 1} did not open a confirmation page",
                "mail.confirmation_message_review",
                "attention",
                {"message_index": observer._mail_review_processed_count + 1},
            )
        return
    if host != "mail.google.com":
        # Nothing to do away from Gmail before a confirmation control is
        # activated; the matched emails are located in the mailbox.
        return
    observer._mail_review_gmail_page = page
    if not observer._mail_search_submitted:
        if _mail_submit_search(observer, page):
            observer._mail_search_submitted = True
            observer._mail_search_wait_ticks = 0
            observer._emit("mail.search_submitted", "searched Gmail for the requested subject line")
        else:
            observer._mail_search_wait_ticks += 1
            if observer._mail_search_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS:
                observer._emit("mail.failed", "Gmail search was not available in the authenticated mailbox", result="failure")
                observer._stop.set()
        return
    if not observer._mail_review_collection_done:
        _mail_collect_review_candidates(observer, page, config)
        return
    if queue_mode and observer._mail_review_processed_count >= _mail_review_queue_limit(config):
        _mail_complete_review_queue(observer)
        return
    if not observer._mail_selection_done:
        _mail_open_next_review_candidate(observer, page, config)
        return
    _mail_handle_reviewed_message(observer, page, config)


_ALERTS_MAX_TICKS = 300
_ALERTS_HUMAN_CHAR_DELAY_MS = 65
_ALERTS_AFTER_TYPE_DELAY_SECONDS = 5.0
_ALERTS_AFTER_CLICK_DELAY_SECONDS = 3.0
_ALERTS_INTER_TOPIC_DELAY_SECONDS = 2.5
_ALERTS_QUOTE_CHARS = "\"'`“”‘’"
_ALERTS_CREATE_LABELS = (
    "create alert", "create an alert", "create the alert", "créer une alerte", "creer une alerte",
    "créer l'alerte", "creer l'alerte", "créer alerte", "creer alerte",
    "crear alerta", "crear una alerta", "alert erstellen", "erstelle einen alert",
    "crea avviso", "criar alerta", "создать оповещение", "创建提醒", "アラートを作成", "알림 만들기",
)
_ALERTS_CREATE_VISIBLE_LABELS = (
    "Create alert", "Create Alert", "Create an alert", "Create the alert",
    "Créer l'alerte", "Créer l’alerte", "Créer une alerte", "Creer l'alerte", "Creer une alerte",
    "Crear una alerta", "Crear alerta", "Alert erstellen", "Crea avviso", "Criar alerta",
)
_ALERTS_CREATE_WORDS = ("create", "créer", "creer", "crear", "erstellen", "erstelle", "crea", "criar", "создать", "创建", "作成", "만들")
_ALERTS_ALERT_WORDS = ("alert", "alerte", "alerta", "avviso", "hinweis", "оповещ", "提醒", "アラート", "알림")


def _alerts_clean_topic(topic: str) -> str:
    """Remove quote characters before a topic is typed into Google Alerts."""
    return "".join(character for character in str(topic or "") if character not in _ALERTS_QUOTE_CHARS).strip()


def _alerts_type_topic(field, topic: str) -> bool:
    """Clear and type a topic with a human-like per-character delay."""
    try:
        field.click(timeout=1500)
        field.press("ControlOrMeta+A", timeout=1500)
        field.press("Backspace", timeout=1500)
        field.type(topic, delay=_ALERTS_HUMAN_CHAR_DELAY_MS, timeout=5000)
        return True
    except Exception:  # noqa: BLE001 - Google can replace the field while typing
        return False


def _alerts_topic_input(page: "Page"):
    """Find Google's alert topic field without depending on generated ids."""
    selectors = (
        'input[name="q"]',
        'input[aria-label*="alert" i]',
        'input[placeholder*="alert" i]',
        'input[type="text"]',
    )
    for selector in selectors:
        try:
            for field in page.locator(selector).all():
                if field.is_visible() and not field.is_disabled():
                    return field
        except Exception:  # noqa: BLE001 - Google can replace the form mid-render
            continue
    return None


def _alerts_click_create(page: "Page") -> bool:
    """Click Google's localized Create Alert action, including dynamic buttons.

    Google Alerts has shipped the action as a button, an input, and a role
    button in different locales and UI revisions. The trusted Playwright
    locator pass runs first, with a DOM/form fallback for unlabeled variants.
    """
    # Prefer Playwright's real locator click. A DOM click from page.evaluate()
    # can fire an untrusted event that Google renders as a click but does not
    # always process as the form submission.
    try:
        # Google often puts the label in a nested span inside the blue button.
        # Clicking the exact visible text still generates a trusted Playwright
        # event and bubbles to the actual submit control.
        for label in _ALERTS_CREATE_VISIBLE_LABELS:
            for control in page.get_by_text(label, exact=True).all():
                if control.is_visible():
                    control.click(timeout=3000)
                    logger.info("session %s: clicked Google Alerts create text via trusted locator (%s)", getattr(page, "url", ""), label)
                    return True
    except Exception:  # noqa: BLE001 - continue with semantic control scanning
        pass
    try:
        controls = page.locator('button, input[type="button"], input[type="submit"], [role="button"], a').all()
    except Exception:  # noqa: BLE001
        controls = []
    for control in controls:
        try:
            if not control.is_visible() or control.is_disabled():
                continue
            label = " ".join(filter(None, [
                control.inner_text(),
                control.get_attribute("aria-label"),
                control.get_attribute("value"),
                control.get_attribute("title"),
                control.get_attribute("data-tooltip"),
            ])).strip()
            normalized = label.lower().replace("’", "'")
            ascii_normalized = unicodedata.normalize("NFD", normalized).encode("ascii", "ignore").decode("ascii")
            create_labels = tuple(candidate.lower() for candidate in _ALERTS_CREATE_LABELS)
            create_words = tuple(word.lower() for word in _ALERTS_CREATE_WORDS)
            alert_words = tuple(word.lower() for word in _ALERTS_ALERT_WORDS)
            ascii_labels = tuple(unicodedata.normalize("NFD", candidate).encode("ascii", "ignore").decode("ascii") for candidate in create_labels)
            ascii_create_words = tuple(unicodedata.normalize("NFD", word).encode("ascii", "ignore").decode("ascii") for word in create_words)
            ascii_alert_words = tuple(unicodedata.normalize("NFD", word).encode("ascii", "ignore").decode("ascii") for word in alert_words)
            matches = any(candidate in normalized for candidate in create_labels) or any(candidate in ascii_normalized for candidate in ascii_labels)
            matches = matches or ((any(word in normalized for word in create_words) or any(word in ascii_normalized for word in ascii_create_words)) and (any(word in normalized for word in alert_words) or any(word in ascii_normalized for word in ascii_alert_words)))
            if matches:
                control.click(timeout=3000)
                logger.info("session %s: clicked Google Alerts create control via trusted locator (%s)", getattr(page, "url", ""), label)
                return True
        except Exception:  # noqa: BLE001 - continue scanning dynamic controls
            continue

    try:
        clicked = page.evaluate(
            """({actionWords, alertWords}) => {
                const normalize = value => String(value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase().replace(/[’']/g, ' ').replace(/\\s+/g, ' ').trim();
                const visible = node => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                };
                const label = node => normalize([
                    node.innerText,
                    node.textContent,
                    node.value,
                    node.getAttribute('aria-label'),
                    node.getAttribute('title'),
                    node.getAttribute('data-tooltip'),
                ].filter(Boolean).join(' '));
                const controls = [...document.querySelectorAll('button, input[type="button"], input[type="submit"], [role="button"]')];
                for (const control of controls) {
                    const text = label(control);
                    const isCreateAlert = actionWords.some(word => text.includes(normalize(word))) && alertWords.some(word => text.includes(normalize(word)));
                    if (visible(control) && isCreateAlert && !control.disabled && control.getAttribute('aria-disabled') !== 'true') {
                        control.click();
                        return {clicked: true, method: 'localized-label', label: text};
    }
}

                // Last-resort structural fallback: the alert editor's form is
                // the only form containing the topic query field. Click its
                // first visible action control even when Google exposes no
                // accessible label in the account's language.
                const topic = document.querySelector('input[name="q"], input[aria-label*="alert" i], input[placeholder*="alert" i]');
                let form = topic && topic.closest('form');
                if (!form && topic) form = topic.parentElement?.closest('form');
                const scoped = form ? [...form.querySelectorAll('button, input[type="button"], input[type="submit"], [role="button"]')] : [];
                for (const control of scoped) {
                    if (visible(control) && !control.disabled && control.getAttribute('aria-disabled') !== 'true') {
                        control.click();
                        return {clicked: true, method: 'alert-form-fallback', label: label(control)};
                    }
                }
                return {clicked: false, method: null, label: ''};
            }""",
            {"actionWords": _ALERTS_CREATE_WORDS, "alertWords": _ALERTS_ALERT_WORDS},
        )
        if isinstance(clicked, dict) and clicked.get("clicked"):
            logger.info("session %s: clicked Google Alerts create control via %s (%s)", getattr(page, "url", ""), clicked.get("method"), clicked.get("label", ""))
            return True
    except Exception:  # noqa: BLE001 - use the locator fallback below
        pass
    try:
        controls = page.locator('button, input[type="button"], input[type="submit"], [role="button"], a').all()
    except Exception:  # noqa: BLE001
        return False
    for control in controls:
        try:
            if not control.is_visible() or control.is_disabled():
                continue
            label = " ".join(filter(None, [
                control.inner_text(),
                control.get_attribute("aria-label"),
                control.get_attribute("value"),
                control.get_attribute("title"),
            ])).strip().lower()
            normalized = label.replace("’", "'")
            matches = any(candidate in normalized for candidate in _ALERTS_CREATE_LABELS)
            matches = matches or (any(word in normalized for word in _ALERTS_CREATE_WORDS) and any(word in normalized for word in _ALERTS_ALERT_WORDS))
            if matches:
                control.click(timeout=1800)
                return True
        except Exception:  # noqa: BLE001 - continue scanning localized controls
            continue
    return False


def _mail_open_reply_thread(page: "Page", config: dict[str, object]) -> bool:
    """Open the first matching Gmail thread with a trusted subject click."""
    display_subject = " ".join(unquote(str(config.get("subject") or "")).split())
    subject = display_subject.lower()
    if not subject:
        return False
    exact = config.get("match_mode") == "exact"
    # Gmail highlights the matching phrase inside the result row. Clicking
    # that rendered subject text is the most stable trusted action across
    # current Gmail layouts and avoids delegated checkbox handlers entirely.
    try:
        text_targets = page.get_by_text(display_subject, exact=False).all()
        for target in text_targets:
            try:
                if target.is_visible(timeout=300):
                    target.click(timeout=2000)
                    return True
            except Exception:  # noqa: BLE001 - the result row can re-render after a click
                continue
    except Exception:  # noqa: BLE001 - fall through to structural row matching
        pass
    row_selectors = (
        '[role="main"] tr.zA',
        '[role="main"] tr',
        '[role="main"] [role="row"]',
        '[role="grid"] [role="row"]',
        'tr.zA',
        '.zA',
    )
    try:
        for selector in row_selectors:
            for row in page.locator(selector).all():
                try:
                    if not row.is_visible(timeout=300):
                        continue
                    row_text = row.inner_text(timeout=600) or ""
                    text = " ".join(row_text.split()).lower()
                    lines = [line.strip().lower() for line in row_text.splitlines() if line.strip()]
                    if not (subject in lines if exact else subject in text):
                        continue
                    # xY is shared by Gmail's checkbox/star cells, so it is
                    # intentionally excluded here. a4W/y6/bog are the
                    # subject-cell variants; the text-matched td fallback
                    # below handles newer layouts.
                    targets = row.locator('td.a4W, td.y6, .bog, [role="link"], a').all()
                    for target in targets:
                        try:
                            if target.is_visible(timeout=300):
                                target.click(timeout=2000)
                                return True
                        except Exception:  # noqa: BLE001 - Gmail can replace a row during navigation
                            continue
                    # Some Gmail layouts render the subject as plain text in
                    # a table cell without the older xY/bog classes.
                    for cell in row.locator('td').all():
                        try:
                            if not cell.is_visible(timeout=300):
                                continue
                            cell_text = " ".join((cell.inner_text(timeout=400) or "").split()).lower()
                            if subject in cell_text:
                                cell.click(timeout=2000)
                                return True
                        except Exception:  # noqa: BLE001 - Gmail can replace a cell during navigation
                            continue
                except Exception:  # noqa: BLE001 - virtualized rows may detach between scans
                    continue
    except Exception:  # noqa: BLE001 - search results can be replaced while loading
        return False
    return False


def _mail_scroll_reply_thread(page: "Page") -> bool:
    """Scroll the largest visible Gmail message pane before finding Reply."""
    # Short messages and some Gmail layouts have no nested scroll container at
    # all. If Reply is already visible, scanning it is more reliable than
    # requiring a scroll operation first.
    if _mail_reply_surface_ready(page):
        return True
    try:
        scrolled = bool(page.evaluate(
            """() => {
                const visible = node => {
                    const rect = node.getBoundingClientRect?.();
                    const style = getComputedStyle(node);
                    return !!rect && rect.width > 0 && rect.height > 0 &&
                        style.visibility !== 'hidden' && style.display !== 'none';
                };
                const candidates = [...document.querySelectorAll('*')]
                    .filter(node => visible(node) && node.scrollHeight > node.clientHeight + 40)
                    .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
                const pane = candidates[0] || document.scrollingElement || document.documentElement;
                const before = pane.scrollTop || 0;
                const amount = Math.max(320, Math.floor((pane.clientHeight || window.innerHeight || 600) * 0.75));
                pane.scrollTop = Math.min(before + amount, pane.scrollHeight || before + amount);
                return pane.scrollTop > before || (pane.scrollHeight || 0) <= (pane.clientHeight || 0) + 40;
            }"""
        ))
        if scrolled:
            return True
        # The DOM is usable even when Gmail reports no scroll movement. Let
        # the caller rescan Reply on subsequent ticks instead of failing the
        # workflow before it reaches the action phase.
        return True
    except Exception:  # noqa: BLE001 - the thread pane may still be mounting
        return _mail_reply_surface_ready(page)


_NEWSLETTER_SUCCESS_TERMS = (
    "thanks for subscribing", "thank you for subscribing", "subscription confirmed", "you are subscribed",
    "you’re subscribed", "you've been subscribed", "successfully subscribed", "check your inbox",
    "thanks for signing up", "thank you for signing up", "you're on the list", "you are on the list",
    "welcome to our newsletter", "welcome to the newsletter", "thanks for joining", "thank you for joining",
    "merci pour votre inscription", "inscription confirmée", "abonnement confirmé", "suscripción confirmada",
    "gracias por suscribirte", "inscrição confirmada", "abonnement réussi",
    "obrigado por se inscrever", "inscricao concluida", "inscrição concluída",
    "danke für ihre anmeldung", "anmeldung erfolgreich", "abonnement bestätigt",
    "grazie per esserti iscritto", "iscrizione completata", "iscrizione confermata",
    "bedankt voor je aanmelding", "inschrijving voltooid", "abonnement bevestigd",
)
_NEWSLETTER_FAILURE_TERMS = (
    "invalid email", "enter a valid email", "already subscribed", "something went wrong", "try again",
    "email address is required", "adresse e-mail invalide", "déjà inscrit", "adresse e-mail requise",
    "correo electrónico no válido", "ya estás suscrito", "campo obligatorio",
    "email inválido", "e-mail inválido", "já está inscrito", "campo obrigatório",
    "ungültige e-mail", "bereits angemeldet", "pflichtfeld",
    "email non valida", "già iscritto", "campo obbligatorio",
    "ongeldig e-mailadres", "al aangemeld", "verplicht veld",
)
_NEWSLETTER_SUBMIT_LABELS = (
    "subscribe", "sign up", "join", "submit", "register", "get updates", "newsletter",
    "s'inscrire", "inscrire", "abonner", "suscrire", "inscrivez", "suscribirme", "suscribirse",
    "suscribir", "registrar", "registrarse", "inscrever", "inscreva", "assinar",
    "anmelden", "abonnieren", "registrieren", "join now", "get the newsletter",
    "rejoindre", "rejoignez", "únete", "unete", "mitmachen", "iscriviti", "abbonati", "aanmelden", "abonneren",
)
_NEWSLETTER_POPUP_ACCEPT_LABELS = (
    "accept all", "accept cookies", "allow all", "agree", "i agree", "consent", "continue", "got it", "ok",
    "tout accepter", "accepter", "j'accepte", "j’accepte", "autoriser", "continuer",
    "aceptar todo", "aceptar", "consentir", "continuar", "entendido",
    "alle akzeptieren", "zustimmen", "akzeptieren", "accetta tutto", "accetta", "aceitar tudo", "aceitar",
)
_NEWSLETTER_POPUP_CLOSE_LABELS = (
    "close", "dismiss", "no thanks", "not now", "maybe later", "continue without", "fermer", "cerrar",
    "ahora no", "más tarde", "schließen", "nicht jetzt", "chiudi", "non ora", "fechar", "agora não", "×", "✕",
)
_NEWSLETTER_POPUP_CONTEXT = (
    "cookie", "cookies", "privacy", "consent", "gdpr", "personal data", "confidentialité", "privacidad",
    "datenschutz", "newsletter", "subscribe", "sign up", "suscrib", "inscri", "abonn",
)
_NEWSLETTER_POPUP_MAX_ACTIONS = 8

# A literal ``input[type="text"]`` candidate matches the first visible text box
# on the page, which for a "city" field is very likely the email or name input.
# Only these specific types are authoritative enough to resolve a field on their
# own; a generic ``text`` type is never used as a candidate.
_NEWSLETTER_SPECIFIC_INPUT_TYPES = ("email", "tel", "url", "number", "date", "month", "week", "time", "search")

# Input types that cannot hold a typed value. Discovery selectors are hints, so
# a loose substring such as ``input[name*="mail" i]`` can match a checkbox named
# ``mailing_list_optin`` ahead of the real email input.
_NEWSLETTER_NON_TEXT_INPUT_TYPES = ("checkbox", "radio", "submit", "button", "hidden", "image", "file", "reset", "range", "color", "password")


def _newsletter_config_lines(observer: "BrowserObserver", key: str) -> list[str]:
    return [line.strip() for line in str(observer.newsletter_config.get(key, "") or "").splitlines() if line.strip()][:8]


def _newsletter_field_records(observer: "BrowserObserver") -> list[dict[str, object]]:
    raw = observer.newsletter_config.get("fields")
    if not isinstance(raw, list):
        return []
    records: list[dict[str, object]] = []
    for item in raw[:40]:
        if not isinstance(item, dict):
            continue
        selector = str(item.get("selector") or "").strip()
        field_key = str(item.get("field_key") or "").strip().lower()
        if selector and field_key:
            records.append({**item, "selector": selector, "field_key": field_key})
    return records


def _newsletter_surfaces(page, include_followup: bool = False):
    """Return page surfaces and frames that visibly contain a signup form.

    Newsletter providers commonly host the actual form in a cross-origin
    embed. Keep unrelated third-party frames out of scope: a cross-origin frame
    is included only when it currently exposes both a visible email field and
    a visible submit-like control.
    """
    surfaces = [page]
    page_host = (urlparse(getattr(page, "url", "") or "").hostname or "").lower().rstrip(".")
    try:
        for frame in page.frames:
            if frame is getattr(page, "main_frame", page):
                continue
            frame_url = getattr(frame, "url", "") or ""
            frame_host = (urlparse(frame_url).hostname or "").lower().rstrip(".")
            if frame_host and page_host and frame_host != page_host:
                try:
                    email_count = frame.locator(
                        'input[type="email"]:visible, input[autocomplete="email"]:visible, input[name*="email" i]:visible, input[name*="courriel" i]:visible, input[name*="correo" i]:visible, input[placeholder*="email" i]:visible, input[placeholder*="courriel" i]:visible, input[placeholder*="correo" i]:visible, input[aria-label*="email" i]:visible, input[aria-label*="courriel" i]:visible, input[aria-label*="correo" i]:visible'
                    ).count()
                    submit_count = frame.locator(
                        'button[type="submit"]:visible, input[type="submit"]:visible, [role="button"]:visible'
                    ).count()
                    followup_count = frame.locator(
                        'input[type="password"]:visible, input[autocomplete="name"]:visible, input[autocomplete="given-name"]:visible, input[autocomplete="family-name"]:visible, input[name*="name" i]:visible'
                    ).count() if include_followup else 0
                    if not (email_count or followup_count) or not submit_count:
                        continue
                except Exception:  # noqa: BLE001 - cross-origin embeds can detach while loading
                    continue
            # A frame without a navigated URL is retained for deterministic
            # test doubles and remains constrained by field semantics.
            surfaces.append(frame)
    except Exception:  # noqa: BLE001 - test doubles and detached pages may not expose frames
        pass
    return surfaces


def _mail_confirmation_surfaces(page):
    """Include embedded confirmation forms even when they ask only for a name."""
    surfaces = [page]
    try:
        frames = page.frames
    except Exception:  # noqa: BLE001 - a landing page may be navigating
        return surfaces
    for frame in frames:
        if frame is getattr(page, "main_frame", page):
            continue
        try:
            if not frame.locator('input:visible, select:visible, textarea:visible').count():
                continue
            controls = frame.locator('button:visible, input[type="submit"]:visible, [role="button"]:visible').all()[:30]
            if any(_mail_confirmation_score(_mail_control_label(control)) >= _MAIL_LANDING_CONFIRMATION_MIN_SCORE for control in controls):
                surfaces.append(frame)
        except Exception:  # noqa: BLE001 - cross-origin frames can detach mid-scan
            continue
    return surfaces


def _newsletter_normalize_label(value: object) -> str:
    text = " ".join(str(value or "").replace("\xa0", " ").split()).casefold()
    return "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))


def _newsletter_popup_control_label(control) -> str:
    values: list[str] = []
    for getter in ("inner_text",):
        try:
            values.append(str(getattr(control, getter)(timeout=400) or ""))
        except Exception:  # noqa: BLE001 - popup controls can detach during hydration
            pass
    for attribute in ("aria-label", "title", "value"):
        try:
            values.append(str(control.get_attribute(attribute) or ""))
        except Exception:  # noqa: BLE001 - some custom controls omit attributes
            pass
    return _newsletter_normalize_label(" ".join(values))


def _handle_newsletter_popup(page: "Page", observer: "BrowserObserver") -> bool:
    """Dismiss safe consent overlays while preserving subscription dialogs.

    Public publishers commonly mount cookie/privacy banners over the real form,
    sometimes after the first page tick.  Only localized consent/deferral
    controls inside a visible modal-like surface are eligible here.  A surface
    that already contains an email field is deliberately left alone so the
    normal signup resolver can fill and submit the subscription popup itself.
    """
    if observer._newsletter_popup_actions >= _NEWSLETTER_POPUP_MAX_ACTIONS:
        return False
    selectors = (
        '[role="dialog"]:visible', '[role="alertdialog"]:visible', '[aria-modal="true"]:visible',
        'dialog[open]:visible', '[class*="cookie" i]:visible', '[class*="consent" i]:visible',
        '[class*="modal" i]:visible', '[class*="popup" i]:visible', '[class*="overlay" i]:visible',
    )
    seen_nodes: set[int] = set()
    for surface in _newsletter_surfaces(page):
        for selector in selectors:
            try:
                nodes = surface.locator(selector).all()
            except Exception:  # noqa: BLE001 - a detached frame is not a blocker
                continue
            for node in nodes[:12]:
                marker = id(node)
                if marker in seen_nodes:
                    continue
                seen_nodes.add(marker)
                try:
                    if not node.is_visible(timeout=300):
                        continue
                    popup_text = _newsletter_normalize_label(node.inner_text(timeout=500))
                    if not popup_text:
                        continue
                    email_count = node.locator(
                        'input[type="email"]:visible, input[autocomplete="email"]:visible, input[name*="email" i]:visible'
                    ).count()
                    if email_count:
                        # This is the subscription popup, not a blocker. Let
                        # the ordinary email/field resolver own it.
                        continue
                    has_privacy_context = any(term in popup_text for term in _NEWSLETTER_POPUP_CONTEXT[:9])
                    controls = node.locator('button:visible, [role="button"]:visible, input[type="button"]:visible, input[type="submit"]:visible, a[aria-label]:visible').all()
                    for control in controls[:20]:
                        try:
                            if not control.is_enabled(timeout=300):
                                continue
                        except Exception:  # noqa: BLE001 - enabled is best effort
                            continue
                        label = _newsletter_popup_control_label(control)
                        accept = has_privacy_context and any(term == label or term in label for term in _NEWSLETTER_POPUP_ACCEPT_LABELS)
                        close = has_privacy_context and any(term == label or term in label for term in _NEWSLETTER_POPUP_CLOSE_LABELS)
                        if not (accept or close):
                            continue
                        observer._newsletter_popup_actions += 1
                        control.click(timeout=1800, no_wait_after=True)
                        observer._emit("newsletter.signup.popup_dismissed", "Dismissed a localized consent or privacy popup before filling the signup form")
                        return True
                except Exception:  # noqa: BLE001 - popup re-renders are normal
                    continue
    return False


def _newsletter_test_identity_value(observer: "BrowserObserver", field_key: str, selector: str | None = None) -> object | None:
    if field_key == "email":
        return str(observer.newsletter_config.get("account_email") or "").strip() or None
    if field_key == "signup_password":
        return str(observer.newsletter_config.get("signup_password") or "") or None
    identity = observer.newsletter_config.get("test_identity")
    if not isinstance(identity, dict):
        return None
    # Selector-scoped values disambiguate repeated field keys such as two
    # different selects both reported as ``custom_select``. Field keys remain
    # supported for compact profiles and backwards compatibility.
    if selector:
        selector_value = identity.get(f"selector:{selector}")
        if selector_value not in (None, ""):
            return selector_value
    value = identity.get(field_key)
    if value not in (None, ""):
        return value
    if field_key == "full_name":
        first = str(identity.get("first_name") or "").strip()
        last = str(identity.get("last_name") or "").strip()
        composed = " ".join(part for part in (first, last) if part)
        return composed or None
    return None


def _newsletter_missing_required_fields(observer: "BrowserObserver") -> list[str]:
    missing: list[str] = []
    for record in _newsletter_field_records(observer):
        field_key = str(record["field_key"])
        if field_key in {"consent", "terms"}:
            continue
        if record.get("required") is True and _newsletter_test_identity_value(observer, field_key, str(record.get("selector") or "")) is None:
            missing.append(field_key)
    return missing


def _newsletter_fill_discovered_fields(page, observer: "BrowserObserver") -> tuple[bool, str]:
    """Fill only explicitly approved identity values and never invent data."""
    records = _newsletter_field_records(observer)
    if not records:
        return True, ""
    missing = _newsletter_missing_required_fields(observer)
    if missing:
        return False, "Required signup fields have no approved test values: " + ", ".join(missing[:6])
    for record in records:
        field_key = str(record["field_key"])
        if field_key == "email":
            continue
        value = _newsletter_test_identity_value(observer, field_key, str(record.get("selector") or ""))
        # Consent and terms are an explicit operator-approved action, not
        # personal data. Required controls may therefore be checked without a
        # test identity value; all other required fields must be mapped above.
        if value is None and field_key in {"consent", "terms"}:
            value = True
        locator = _newsletter_field_locator(page, record, observer)
        if locator is None:
            if record.get("required") is True:
                # Discovery selectors are hints. Consent controls are often
                # re-rendered with a new id after the email is entered; let
                # the live multilingual scan resolve them below.
                if field_key in {"consent", "terms"}:
                    continue
                return False, f"Required signup field {field_key} was not visible or enabled"
            continue
        if value is None:
            continue
        field_type = str(record.get("field_type") or record.get("type") or "text").lower()
        try:
            if field_type in {"checkbox", "radio"} or field_key in {"consent", "terms", "marketing_preference"}:
                checked = value is True or str(value).lower() in {"true", "1", "yes", "on", "checked"}
                if checked:
                    _newsletter_check_choice(locator)
                elif field_type == "checkbox" and locator.is_checked(timeout=500):
                    locator.uncheck(timeout=1200)
            elif field_type == "select" or field_key == "custom_select":
                try:
                    locator.select_option(str(value), timeout=1200)
                except Exception:
                    locator.select_option(label=str(value), timeout=1200)
            else:
                _newsletter_fill_text(locator, str(value))
            observer._record_newsletter_skill_action({
                "kind": "check" if field_type in {"checkbox", "radio"} or field_key in {"consent", "terms", "marketing_preference"} else "fill",
                "target": "field",
                "field_key": field_key,
            })
        except Exception as exc:  # noqa: BLE001 - public forms change their controls frequently
            return False, f"Signup field {field_key} could not be filled: {type(exc).__name__}"
    return True, ""


def _newsletter_fill_adaptive_fields(page, observer) -> tuple[bool, str, int]:
    """Let learning mode map approved identity values to live controls."""
    if not observer._newsletter_adaptive_learning:
        return True, "", 0
    identity = observer.newsletter_config.get("test_identity")
    if not isinstance(identity, dict):
        return True, "", 0
    filled = 0
    # The keys that have a generated value of their own. The account mailbox is
    # deliberately absent: it comes from the run's own account record, never
    # from a stored identity value, so a mapping saved under an email key can
    # never displace it.
    for field_key in sorted(GENERATED_FIELD_KEYS):
        value = identity.get(field_key)
        if value in (None, ""):
            continue
        locator = _newsletter_agent_locator(
            page,
            observer,
            "field",
            {"field_key": field_key, "field_type": "text", "label": field_key.replace("_", " ")},
        )
        if locator is None:
            continue
        try:
            control_type = str(locator.get_attribute("type") or "").lower()
            if control_type in {"checkbox", "radio"}:
                if str(value).lower() in {"true", "1", "yes", "on", "checked"}:
                    _newsletter_check_choice(locator)
            else:
                try:
                    tag_name = str(locator.evaluate("element => element.tagName.toLowerCase()") or "").lower()
                except Exception:
                    tag_name = ""
                if tag_name == "select":
                    try:
                        locator.select_option(label=str(value), timeout=1200)
                    except Exception:
                        locator.select_option(str(value), timeout=1200)
                else:
                    _newsletter_fill_text(locator, str(value))
            observer._record_newsletter_skill_action({"kind": "fill", "target": "field", "field_key": field_key})
            filled += 1
        except Exception as exc:  # noqa: BLE001 - live public forms can rerender between decisions
            return False, f"Approved adaptive field {field_key} could not be filled: {type(exc).__name__}", filled
    return True, "", filled


def _newsletter_verify_retained_fields(page, observer) -> tuple[bool, str]:
    """Re-resolve filled controls after reactive forms have settled.

    Several public forms replace their input node after the first input event.
    Verifying the original locator is therefore insufficient: it can still
    report the value that was present on the detached node while the live form
    has reverted to an empty or partial value. Re-resolve each mapped control
    from the current page/frame and verify the expected values without
    including them in diagnostics.
    """
    for record in _newsletter_field_records(observer):
        field_key = str(record["field_key"])
        if field_key in {"consent", "terms"}:
            continue
        expected = _newsletter_test_identity_value(observer, field_key, str(record.get("selector") or ""))
        if expected is None:
            continue
        locator = _newsletter_field_locator(page, record, observer)
        if locator is None:
            if record.get("required") is True:
                return False, f"Required signup field {field_key} disappeared after filling"
            continue
        try:
            actual = str(locator.input_value(timeout=800))
            if field_key != "signup_password":
                actual = actual.strip()
        except Exception as exc:  # noqa: BLE001
            if record.get("required") is True:
                return False, f"Required signup field {field_key} could not be verified: {type(exc).__name__}"
            continue
        expected_value = str(expected) if field_key == "signup_password" else str(expected).strip()
        if actual != expected_value:
            try:
                _newsletter_fill_text(locator, str(expected))
                actual = str(locator.input_value(timeout=800))
                if field_key != "signup_password":
                    actual = actual.strip()
            except Exception as exc:  # noqa: BLE001
                return False, f"Signup field {field_key} did not retain its complete value: {type(exc).__name__}"
            if actual != expected_value:
                return False, f"Signup field {field_key} did not retain its complete value"
    return True, ""


# One page read of every form control, in document order, carrying everything
# the shared classifier needs. The index is the position in this same
# ``querySelectorAll`` result, so it can be used as a locator fallback when a
# control has no usable attribute selector of its own.
_NEWSLETTER_LIVE_CONTROLS_JS = """(config) => {
    const allControls = Array.from(document.querySelectorAll('input, select, textarea, [role="checkbox"]'));
    const visible = (el) => {
        try {
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
        } catch (error) { return false; }
    };
    const labelFor = (el) => {
        const parts = [];
        try {
            for (const label of (el.labels || [])) parts.push(label.innerText || label.textContent || '');
        } catch (error) { /* detached or non-standard node */ }
        try {
            const wrapping = el.closest('label');
            if (wrapping) parts.push(wrapping.innerText || wrapping.textContent || '');
            const parent = el.parentElement;
            if (parent) for (const label of parent.querySelectorAll(':scope > label, label')) parts.push(label.innerText || label.textContent || '');
        } catch (error) { /* custom controls may not expose a normal parent */ }
        const labelledBy = el.getAttribute('aria-labelledby') || '';
        for (const id of labelledBy.split(/\\s+/)) {
            if (!id) continue;
            const node = document.getElementById(id);
            if (node) parts.push(node.innerText || node.textContent || '');
        }
        if ((el.type || '').toLowerCase() === 'checkbox' || el.getAttribute('role') === 'checkbox') {
            let row = el.parentElement;
            for (let depth = 0; row && depth < 4; depth++, row = row.parentElement) {
                if (row.querySelectorAll('input[type="checkbox"], [role="checkbox"]').length !== 1) continue;
                const text = (row.innerText || '').trim();
                if (text && text.length <= 250) { parts.push(text); break; }
            }
        }
        return parts.join(' ').replace(/\\s+/g, ' ').trim().slice(0, 200);
    };
    const emailFields = allControls.filter((el) => {
        if (!visible(el)) return false;
        const text = [el.getAttribute('type'), el.getAttribute('autocomplete'), el.getAttribute('name'), el.id, el.getAttribute('aria-label'), el.getAttribute('placeholder'), labelFor(el)].join(' ').toLowerCase().normalize('NFKD').replace(/[\\u0300-\\u036f]/g, '');
        return /\\bemail\\b|e-mail|\\bmail\\b|\\bcourriel\\b|correo\\s+electronico|direccion\\s+de\\s+correo|endereco\\s+de\\s+email|adresse\\s+(?:e-)?mail|elektronische\\s+mail/.test(text);
    });
    const namePattern = /\\b(first|last|given|family|full|middle)\\W*name\\b|\\bname\\b|prenom|apellido|surname|vorname|nachname|cognome|sobrenome|nome/;
    const followupFields = config && config.includeNameStep
        ? allControls.filter((el) => {
            if (!visible(el)) return false;
            const text = [el.getAttribute('autocomplete'), el.getAttribute('name'), el.id, el.getAttribute('aria-label'), el.getAttribute('placeholder'), labelFor(el)].join(' ').toLowerCase().normalize('NFKD').replace(/[\\u0300-\\u036f]/g, '');
            const emailConfirmation = /(email|e-mail|mail|courriel|correo|adresse|endereco)/.test(text) && /(confirm|confirmation|verify|verification|retype|repeat|again|confirmar|verificar|confirmer|verifier|wiederholen|bestatigen|repetir)/.test(text);
            const agreement = (el.getAttribute('type') || '').toLowerCase() === 'checkbox' && /(agree|accept|understand|consent|terms|conditions|privacy|rules|acepto|aceptar|entiendo|concordo|entendo|accepte|terminos|condiciones|termos|condicoes|nutzungsbedingungen|datenschutz|zustimm|akzept|bedingungen)/.test(text);
            return namePattern.test(text) || emailConfirmation || agreement || (el.getAttribute('type') || '').toLowerCase() === 'password';
        })
        : [];
    const roots = new Set();
    const signupRoot = (el) => {
        const semantic = el.closest('form, [role="form"], [role="dialog"], dialog, [class*="newsletter" i], [id*="newsletter" i], [class*="subscribe" i], [id*="subscribe" i], [class*="signup" i], [id*="signup" i]');
        if (semantic) return semantic;
        let node = el.parentElement;
        for (let depth = 0; node && depth < 4 && node !== document.body; depth += 1, node = node.parentElement) {
            if (node.querySelectorAll('input, select, textarea, [role="checkbox"]').length <= 40) return node;
        }
        return null;
    };
    for (const field of [...emailFields, ...followupFields]) {
        const root = signupRoot(field);
        if (root) roots.add(root);
    }
    if (config && config.includeConfirmationStep) {
        for (const form of document.querySelectorAll('form, [role="form"], [role="dialog"], dialog')) {
            const controls = form.querySelectorAll('input, select, textarea, [role="checkbox"]');
            if (!controls.length || ![...controls].some(visible)) continue;
            const actions = [...form.querySelectorAll('button, input[type="submit"], [role="button"]')];
            if (actions.some(el => visible(el) && /confirm|verify|subscribe|complete|finish|continue|submit|confirmer|verifier|abonner|s'inscrire/i.test((el.innerText || el.value || el.getAttribute('aria-label') || '').trim()))) roots.add(form);
        }
    }
    const selected = new Set();
    for (const root of roots) {
        for (const control of root.querySelectorAll('input, select, textarea, [role="checkbox"]')) selected.add(control);
    }
    const controls = allControls.filter((el) => selected.has(el));
    const originalIndex = new Map(allControls.map((el, index) => [el, index]));
    return controls.map((el, index) => {
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        let style = null;
        try { style = window.getComputedStyle(el); } catch (error) { style = null; }
        const options = tag === 'select'
            ? Array.from(el.options || []).map((option) => ({
                value: option.value,
                label: (option.textContent || '').replace(/\\s+/g, ' ').trim(),
            }))
            : [];
        return {
            index: originalIndex.get(el) ?? index,
            tag: tag,
            type: tag === 'input' ? type : (el.getAttribute('role') === 'checkbox' ? 'checkbox' : tag),
            name: el.getAttribute('name') || '',
            id: el.getAttribute('id') || '',
            aria_label: el.getAttribute('aria-label') || '',
            title: el.getAttribute('title') || '',
            placeholder: el.getAttribute('placeholder') || '',
            autocomplete: el.getAttribute('autocomplete') || '',
            label: labelFor(el),
            required: el.required === true || el.getAttribute('aria-required') === 'true',
            hidden: type === 'hidden' || !visible(el) || (style !== null && (style.display === 'none' || style.visibility === 'hidden')),
            disabled: el.disabled === true || el.readOnly === true || el.getAttribute('aria-disabled') === 'true',
            value: el.value === undefined || el.value === null ? '' : String(el.value),
            checked: el.checked === true || el.getAttribute('aria-checked') === 'true',
            selected_index: tag === 'select' ? el.selectedIndex : -1,
            options: options,
        };
    });
}"""

def _newsletter_live_controls(surface, include_name_step: bool = False, include_confirmation_step: bool = False) -> list[dict[str, object]]:
    """Controls inside the current signup form, excluding unrelated page inputs."""
    try:
        raw = surface.evaluate(_NEWSLETTER_LIVE_CONTROLS_JS, {"includeNameStep": bool(include_name_step), "includeConfirmationStep": bool(include_confirmation_step)})
    except Exception:  # noqa: BLE001 - one unreadable frame must not stop the fill
        return []
    if not isinstance(raw, list):
        return []
    controls: list[dict[str, object]] = []
    for item in raw[:120]:
        if isinstance(item, dict):
            controls.append(item)
    return controls


def _newsletter_live_selectors(control: dict[str, object]) -> list[str]:
    """Exact attribute selectors for one live control, strongest first.

    The document-order index is only a fallback: Playwright's CSS engine
    pierces open shadow roots where ``querySelectorAll`` does not, so an index
    can address a different control than the one it was read from.
    """
    tag = str(control.get("tag") or "input").strip().lower()
    if not re.fullmatch(r"[a-z]{3,10}", tag):
        tag = "input"
    selectors: list[str] = []
    node_id = str(control.get("id") or "").strip()
    if node_id and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,119}", node_id):
        selectors.append(f"#{node_id}")
    for attribute in ("name", "aria_label", "title", "placeholder"):
        value = str(control.get(attribute) or "").strip()
        key = "aria-label" if attribute == "aria_label" else attribute
        if value and re.fullmatch(r"[A-Za-z0-9_ .,:@/-]{1,120}", value):
            selectors.append(f'{tag}[{key}="{value}"]')
    return selectors


def _newsletter_live_locator(surface, control: dict[str, object]):
    expected_type = str(control.get("type") or "").lower()

    def matches_type(candidate) -> bool:
        if expected_type != "password":
            return True
        try:
            return str(candidate.get_attribute("type") or "").lower() == "password"
        except Exception:  # noqa: BLE001 - a re-rendered control is not a safe match
            return False

    for selector in _newsletter_live_selectors(control):
        try:
            candidate = surface.locator(selector).first
            if candidate.count() and candidate.is_visible(timeout=400) and candidate.is_enabled(timeout=400) and matches_type(candidate):
                return candidate
        except Exception:  # noqa: BLE001 - a stale selector is expected on live forms
            continue
    try:
        index = int(control.get("index", -1))
    except (TypeError, ValueError):
        return None
    if index < 0:
        return None
    try:
        candidate = surface.locator('input, select, textarea, [role="checkbox"]').nth(index)
        return candidate if candidate.count() and matches_type(candidate) else None
    except Exception:  # noqa: BLE001
        return None


def _newsletter_check_choice(locator) -> None:
    """Check native and ARIA checkbox controls without falling back to JS."""
    try:
        locator.check(timeout=1200)
    except Exception:
        # ``check`` is not supported by a div[role=checkbox]; its accessible
        # click action is the equivalent user operation for that control.
        locator.click(timeout=1200)
def _newsletter_live_control_state(control: dict[str, object]) -> str:
    """How one control stands before any fill: ``empty``, ``set`` or ``na``."""
    tag = str(control.get("tag") or "input").lower()
    input_type = str(control.get("type") or "").lower()
    if input_type in {"checkbox", "radio"}:
        return "set" if control.get("checked") is True else "empty"
    if tag == "select":
        options = control.get("options")
        options = options if isinstance(options, list) else []
        try:
            selected = int(control.get("selected_index", -1))
        except (TypeError, ValueError):
            selected = -1
        if selected <= 0 or not options:
            return "empty"
        chosen = options[selected] if selected < len(options) else {}
        label = str((chosen or {}).get("label") or "").strip().lower()
        value = str((chosen or {}).get("value") or "").strip()
        if not value or any(term == label for term in ("select one", "choose one", "select", "choose")):
            return "empty"
        return "set"
    return "set" if str(control.get("value") or "").strip() else "empty"


def _newsletter_live_label(control: dict[str, object]) -> str:
    for key in ("label", "aria_label", "placeholder", "name", "id"):
        value = str(control.get(key) or "").strip()
        if value:
            return value[:60]
    return f"control {control.get('index')}"


def _newsletter_fill_live_fields(page, observer: "BrowserObserver") -> tuple[bool, str, int]:
    """Fill and re-scan controls as reactive signup forms mount their fields."""
    filled = 0
    for pass_number in range(3):
        ready, detail, count = _newsletter_fill_live_fields_once(page, observer)
        filled += count
        if not ready:
            return False, detail, filled
        if pass_number == 2:
            break
        # Frameworks commonly mount confirmation and agreement controls on a
        # subsequent render after the email input event. Bounded fresh reads
        # catch those controls without waiting indefinitely or submitting twice.
        time.sleep(min(0.25, max(0.1, observer._interval)))
    return True, "", filled


def _newsletter_fill_live_fields_once(page, observer: "BrowserObserver") -> tuple[bool, str, int]:
    """Fill live controls that neither the profile nor the approved values cover.

    A run with no saved profile has only the page to work from, and even a
    profiled run meets forms that render extra controls after the first input.
    Both arrive here: each live control is classified through the shared
    vocabulary, its value comes from the approved identity — operator-approved
    first, generated placeholder last — and a control that cannot be filled
    honestly is named rather than invented. Only an explicitly saved publisher
    password can fill a real password input; verification and payment controls
    remain untouched, and the account mailbox is never replaced.
    """
    filled = 0
    generated_keys: list[str] = []
    unresolved: list[str] = []
    failed: str | None = None
    for surface in _newsletter_surfaces(page, include_followup=observer._newsletter_submit_attempted):
        for control in _newsletter_live_controls(
            surface,
            include_name_step=observer._newsletter_submit_attempted,
        ):
            if control.get("hidden") or control.get("disabled"):
                continue
            state = _newsletter_live_control_state(control)
            field_key = semantic_field_key(control)
            required = control.get("required") is True
            is_select = str(control.get("tag") or "").lower() == "select"
            is_choice = str(control.get("type") or "").lower() in {"checkbox", "radio"}
            agreement_key = agreement_checkbox_key(control)
            if agreement_key is not None:
                # This is a live fallback for forms whose checkbox is mounted
                # after the email field or whose id/name changed since
                # discovery. It only handles an explicit legal agreement;
                # marketing/newsletter opt-ins are rejected by the shared
                # classifier above.
                if state == "empty":
                    locator = _newsletter_live_locator(surface, control)
                    if locator is None:
                        unresolved.append(_newsletter_live_label(control))
                        continue
                    try:
                        _newsletter_check_choice(locator)
                        observer._record_newsletter_skill_action({"kind": "check", "target": "field", "field_key": agreement_key})
                        filled += 1
                    except Exception:  # noqa: BLE001
                        unresolved.append(_newsletter_live_label(control))
                continue
            if field_key in {"consent", "terms"}:
                # A required consent control classified by the shared
                # vocabulary is still handled when its label is not one of
                # the stronger phrases above.
                if required and is_choice and state == "empty":
                    locator = _newsletter_live_locator(surface, control)
                    if locator is None:
                        unresolved.append(_newsletter_live_label(control))
                        continue
                    try:
                        _newsletter_check_choice(locator)
                        observer._record_newsletter_skill_action({"kind": "check", "target": "field", "field_key": field_key})
                        filled += 1
                    except Exception:  # noqa: BLE001
                        unresolved.append(_newsletter_live_label(control))
                continue
            if field_key == "signup_password":
                value = _newsletter_test_identity_value(observer, field_key)
                if value is None:
                    if required:
                        unresolved.append("Publisher signup password is required; set it for this account and newsletter")
                    continue
                if state == "set" and str(control.get("value") or "") == str(value):
                    continue
                locator = _newsletter_live_locator(surface, control)
                if locator is None:
                    unresolved.append("Publisher signup password field could not be located")
                    continue
                try:
                    _newsletter_fill_text(locator, str(value))
                    filled += 1
                    generated_keys.append(field_key)
                except Exception:  # noqa: BLE001 - never include secret values in details
                    unresolved.append("Publisher signup password field could not be filled")
                continue
            reason = blocked_live_control_reason(control)
            if reason is not None:
                # Never typed into, checked before the email branch so a
                # password box named ``email`` cannot be given the mailbox. A
                # required one is the operator's to resolve, so it is named
                # rather than skipped silently.
                if required and state == "empty":
                    unresolved.append(f"{_newsletter_live_label(control)} ({reason})")
                continue
            if field_key == "email":
                # Not generated: the account's own mailbox. A form that asks for
                # the address twice renders a second control the primary email
                # locator never filled, so every empty email control is typed
                # from the same owned address.
                mailbox = _newsletter_test_identity_value(observer, "email")
                if mailbox is None:
                    continue
                if state == "set" and str(control.get("value") or "").strip().casefold() == str(mailbox).strip().casefold():
                    continue
                locator = _newsletter_live_locator(surface, control)
                if locator is None:
                    if required:
                        unresolved.append(_newsletter_live_label(control))
                    continue
                try:
                    _newsletter_fill_text(locator, str(mailbox))
                    filled += 1
                except Exception:  # noqa: BLE001
                    if required:
                        unresolved.append(_newsletter_live_label(control))
                continue
            if field_key == "marketing_preference":
                # A subscription preference is the operator's decision. An
                # approved value is honoured; no value is ever invented.
                if is_choice and state == "empty":
                    approved = _newsletter_test_identity_value(observer, field_key)
                    if approved is None:
                        continue
                    locator = _newsletter_live_locator(surface, control)
                    if locator is None:
                        continue
                    try:
                        if str(approved).lower() in {"true", "1", "yes", "on", "checked"}:
                            _newsletter_check_choice(locator)
                    except Exception:  # noqa: BLE001
                        continue
                    continue
                continue
            if state == "set":
                continue
            if field_key == "custom_select":
                if not is_select or not required:
                    continue
                selected = _newsletter_select_generated_option(surface, control, observer)
                if selected:
                    filled += 1
                else:
                    unresolved.append(_newsletter_live_label(control))
                continue
            if field_key not in LIVE_FILL_KEYS:
                # Free text, an unclassified choice, or a control whose purpose
                # is not identifiable. The app does not write prose it cannot
                # justify, so a required one is reported by name instead.
                if required:
                    unresolved.append(_newsletter_live_label(control))
                continue
            value = _newsletter_test_identity_value(observer, field_key)
            if value is None:
                if required:
                    unresolved.append(_newsletter_live_label(control))
                continue
            locator = _newsletter_live_locator(surface, control)
            if locator is None:
                if required:
                    unresolved.append(_newsletter_live_label(control))
                continue
            try:
                _newsletter_fill_text(locator, str(value))
            except Exception as exc:  # noqa: BLE001 - public forms change their controls frequently
                failed = f"Signup field {field_key} could not be filled: {type(exc).__name__}"
                break
            filled += 1
            generated_keys.append(field_key)
            observer._record_newsletter_skill_action({"kind": "fill", "target": "field", "field_key": field_key})
        if failed is not None:
            break
    if failed is not None:
        return False, failed, filled
    if unresolved:
        return False, "Required signup fields could not be filled from the approved profile: " + ", ".join(sorted(dict.fromkeys(unresolved))[:6]), filled
    retained, retained_detail = _newsletter_verify_live_fields(
        page,
        observer,
        generated_keys,
        include_name_step=observer._newsletter_submit_attempted,
    )
    if not retained:
        return False, retained_detail, filled
    if generated_keys:
        observer._emit("newsletter.signup.fields_generated", "Resolved " + str(len(set(generated_keys))) + " live signup field(s) from the approved profile: " + ", ".join(sorted(set(generated_keys))[:8]))
    return True, "", filled


def _newsletter_required_agreements_ready(page, observer: "BrowserObserver") -> bool:
    """Re-scan live signup controls and check/verify explicit legal agreements."""
    pending = [
        str(record.get("field_key") or "")
        for record in _newsletter_field_records(observer)
        if record.get("required") is True and str(record.get("field_key") or "") in {"consent", "terms"}
    ]
    for record in _newsletter_field_records(observer):
        key = str(record.get("field_key") or "")
        if key not in pending:
            continue
        locator = _newsletter_field_locator(page, record, observer)
        if locator is None:
            continue
        try:
            if locator.is_checked(timeout=500):
                pending.remove(key)
        except Exception:  # noqa: BLE001 - a public form may re-render between checks
            continue
    # The form can mount legal controls only after email/name input events.
    # Re-read the live form instead of trusting discovery's earlier snapshot.
    # Only explicit legal agreements are checked; marketing opt-ins stay off.
    pending_live: list[str] = []
    for attempt in range(2):
        pending_live = []
        for surface in _newsletter_surfaces(page):
            for control in _newsletter_live_controls(
                surface,
                include_name_step=observer._newsletter_submit_attempted,
            ):
                if control.get("hidden") or control.get("disabled"):
                    continue
                key = agreement_checkbox_key(control)
                if key is None and control.get("required") is True:
                    candidate_key = semantic_field_key(control)
                    key = candidate_key if candidate_key in {"consent", "terms"} else None
                if key is None:
                    continue
                locator = _newsletter_live_locator(surface, control)
                if locator is None:
                    pending_live.append(key)
                    continue
                try:
                    if locator.is_checked(timeout=500):
                        if key in pending:
                            pending.remove(key)
                        continue
                    if attempt == 0:
                        _newsletter_check_choice(locator)
                        observer._record_newsletter_skill_action({"kind": "check", "target": "field", "field_key": key})
                        if locator.is_checked(timeout=700):
                            if key in pending:
                                pending.remove(key)
                            continue
                    pending_live.append(key)
                except Exception:  # noqa: BLE001 - scan other controls after a stale custom checkbox
                    pending_live.append(key)
        if not pending and not pending_live:
            return True
        if attempt == 0:
            time.sleep(min(0.15, max(0.05, observer._interval)))
    return not pending and not pending_live


def _newsletter_select_generated_option(surface, control: dict[str, object], observer: "BrowserObserver") -> bool:
    """Choose one real option of a live select, or report that none fits.

    Only an option the page actually offers is ever chosen, and an explicit
    opt-out ("prefer not to say") beats a substantive one, so a generated
    identity never asserts an attribute about a person that it need not.
    """
    options = control.get("options")
    options = options if isinstance(options, list) else []
    labels = [str((option or {}).get("label") or "") for option in options if isinstance(option, dict)]
    index = preferred_option_index(labels)
    if index is None:
        return False
    chosen = options[index] if index < len(options) else {}
    value = str((chosen or {}).get("value") or "")
    container = str(control.get("id") or "").strip()
    locator = None
    if container and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,119}", container):
        try:
            candidate = surface.locator(f"select#{container}").first
            locator = candidate if candidate.count() else None
        except Exception:  # noqa: BLE001
            locator = None
    if locator is None:
        locator = _newsletter_live_locator(surface, control)
    if locator is None:
        return False
    try:
        locator.select_option(value=value, timeout=1500)
    except Exception:  # noqa: BLE001
        try:
            locator.select_option(label=str((chosen or {}).get("label") or ""), timeout=1500)
        except Exception:  # noqa: BLE001
            return False
    observer._record_newsletter_skill_action({"kind": "fill", "target": "field", "field_key": "custom_select"})
    return True


def _newsletter_live_observed(page, include_name_step: bool = False) -> dict[str, str]:
    """The value each live control holds right now, keyed by classified field.

    Read from a fresh enumeration of the live form rather than from the node
    that was filled: a reactive framework replaces the input element on the
    first input event, so the original node can report a value the form no
    longer has.
    """
    observed: dict[str, str] = {}
    for surface in _newsletter_surfaces(page, include_followup=include_name_step):
        for control in _newsletter_live_controls(surface, include_name_step=include_name_step):
            field_key = semantic_field_key(control)
            if field_key not in observed:
                value = str(control.get("value") or "")
                observed[field_key] = value if field_key == "signup_password" else value.strip()
    return observed


def _newsletter_verify_live_fields(
    page,
    observer: "BrowserObserver",
    field_keys: list[str],
    include_name_step: bool = False,
) -> tuple[bool, str]:
    """Confirm the live fills survived the form's own re-render.

    A control that lost its value is refilled once, because that is the failure
    a single retry reliably fixes. What is then re-read is the live form again,
    not the node that was written to, so a form that keeps discarding the value
    is reported rather than passed on to submission.
    """
    expected: dict[str, str] = {}
    for field_key in dict.fromkeys(field_keys):
        value = _newsletter_test_identity_value(observer, field_key)
        if value is not None:
            expected[field_key] = str(value) if field_key == "signup_password" else str(value).strip()
    if not expected:
        return True, ""
    missing: dict[str, str] = dict(expected)
    for attempt in (1, 2):
        observed = _newsletter_live_observed(page, include_name_step=include_name_step)
        missing = {key: value for key, value in expected.items() if observed.get(key) != value}
        if not missing:
            return True, ""
        if attempt == 2:
            break
        for surface in _newsletter_surfaces(page, include_followup=include_name_step):
            for control in _newsletter_live_controls(surface, include_name_step=include_name_step):
                field_key = semantic_field_key(control)
                if field_key not in missing:
                    continue
                locator = _newsletter_live_locator(surface, control)
                if locator is None:
                    continue
                try:
                    _newsletter_fill_text(locator, missing[field_key])
                except Exception:  # noqa: BLE001 - a control that cannot be refilled is reported below
                    continue
    return False, "Signup field " + ", ".join(sorted(missing)[:4]) + " did not retain its value after the form re-rendered"


def _newsletter_fill_text(locator, value: str) -> None:
    """Set a public form field and verify the complete value was accepted.

    A few public forms re-render their input after the first input event. A
    bare fill can then leave only a prefix (or one character) in the newly
    mounted node. Re-read the value and retry once with a real keyboard fill;
    never proceed to submit a partially populated form.
    """
    expected = str(value)
    try:
        locator.fill(expected, timeout=1500)
    except Exception:
        # A framework can detach the node while the public form hydrates it.
        # Continue into the verified native-setter/keyboard fallbacks instead
        # of treating that transient fill timeout as a failed signup.
        pass
    try:
        actual = str(locator.input_value(timeout=600))
    except Exception:
        actual = ""
    if actual == expected:
        # Commit the browser's native input/change state. Some newsletter
        # widgets enable their submit control only after the field blurs.
        try:
            locator.press("Tab", timeout=800)
        except Exception:
            pass
        return
    # If the site re-mounts the input during an input event, set the native
    # value atomically and dispatch the same events its form listeners use.
    # This avoids losing the remainder of a value to a one-character
    # re-rendering loop while still exercising the page's own validation.
    try:
        locator.evaluate(
            """(element, nextValue) => {
                const prototype = element instanceof HTMLTextAreaElement
                    ? HTMLTextAreaElement.prototype
                    : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
                if (!setter) throw new Error('value setter unavailable');
                setter.call(element, nextValue);
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            expected,
        )
        if str(locator.input_value(timeout=800)) == expected:
            try:
                locator.press("Tab", timeout=800)
            except Exception:
                pass
            return
    except Exception:
        pass
    locator.press("Control+A", timeout=800)
    locator.type(expected, delay=12, timeout=4000)
    try:
        actual = str(locator.input_value(timeout=800))
    except Exception:
        actual = ""
    if actual != expected:
        raise RuntimeError("the public signup field did not retain its complete value")
    try:
        locator.press("Tab", timeout=800)
    except Exception:
        pass


def _newsletter_locator_is_usable(locator) -> bool:
    """Visibility, enablement, and readonly gate shared by every resolver.

    A readonly control can be visible and enabled but is not an actionable
    signup field. A locator that exposes no ``get_attribute`` — test doubles,
    mostly — is accepted rather than rejected.
    """
    try:
        if not locator.count() or not locator.is_visible(timeout=800) or not locator.is_enabled(timeout=800):
            return False
    except Exception:  # noqa: BLE001 - public forms change their markup frequently
        return False
    get_attribute = getattr(locator, "get_attribute", None)
    if get_attribute is None:
        return True
    try:
        readonly = get_attribute("readonly")
    except Exception:  # noqa: BLE001
        return True
    if str(readonly or "").lower() not in {"", "readonly", "true"}:
        return True
    return readonly is None


def _newsletter_locator(page, selector: str):
    if not selector or len(selector) > 300:
        return None
    for surface in _newsletter_surfaces(page):
        try:
            locator = surface.locator(selector).first
        except Exception:  # noqa: BLE001 - public forms change their markup frequently
            continue
        if _newsletter_locator_is_usable(locator):
            return locator
    return None


def _newsletter_label_locator(page, text: str):
    """Resolve a control through its associated ``<label>``.

    A form with a generated id and an opaque name still carries a human label,
    and Playwright associates it through ``for=`` or a wrapping label. This is
    the strongest signal available for a field whose semantic attributes were
    rewritten by the site's framework.
    """
    text = str(text or "").strip()
    if not text or len(text) > 120 or not re.search(r"[A-Za-z]{2}", text):
        return None
    for surface in _newsletter_surfaces(page):
        get_by_label = getattr(surface, "get_by_label", None)
        if get_by_label is None:
            continue
        try:
            locator = get_by_label(text).first
        except Exception:  # noqa: BLE001 - public forms change their markup frequently
            continue
        if _newsletter_locator_is_usable(locator):
            return locator
    return None


def _newsletter_locator_type_is_text(locator) -> bool:
    """Reject a resolved control whose input type cannot hold a typed value.

    An unknown type is accepted, so a page that omits ``type`` still resolves.
    """
    get_attribute = getattr(locator, "get_attribute", None)
    if get_attribute is None:
        return True
    try:
        raw = str(get_attribute("type") or "").strip().lower()
    except Exception:  # noqa: BLE001
        return True
    return not raw or raw not in _NEWSLETTER_NON_TEXT_INPUT_TYPES


def _newsletter_skill_locator(
    page,
    observer: "BrowserObserver",
    target: str,
    context: dict[str, str] | None = None,
):
    """Replay a learned control only after validating it against the live DOM."""
    skill = observer.newsletter_config.get("learned_skill")
    if not isinstance(skill, dict):
        return None
    field_key = str((context or {}).get("field_key") or "")
    for action in skill.get("actions", []) if isinstance(skill.get("actions"), list) else []:
        if not isinstance(action, dict) or action.get("kind") != "locate":
            continue
        if str(action.get("target") or "") != target:
            continue
        if target == "field" and field_key and str(action.get("field_key") or "") != field_key:
            continue
        selectors: list[str] = []
        saved_selector = str(action.get("selector") or "")
        if saved_selector:
            selectors.append(saved_selector)
        control = action.get("control") if isinstance(action.get("control"), dict) else {}
        tag = str(control.get("tag") or "input").lower()
        if tag not in {"input", "textarea", "select", "button", "div", "span", "a"}:
            tag = "input" if target in {"email_field", "field"} else "button"
        for attribute in ("id", "name", "aria_label", "title", "placeholder"):
            value = str(control.get(attribute) or "").strip()
            if value and len(value) <= 160 and all(char not in value for char in ('"', "'", "\\", "\n", "\r")):
                css_attribute = "aria-label" if attribute == "aria_label" else attribute
                selectors.append(f'{tag}[{css_attribute}="{value}"]:visible')
        for selector in selectors[:8]:
            locator = _newsletter_locator(page, selector)
            if locator is not None:
                observer._newsletter_skill_replayed = True
                observer._emit("newsletter.skill.validated", f"Validated a saved website skill control for {target}", result="success")
                if not observer._newsletter_skill_replay_reported:
                    observer._newsletter_skill_replay_reported = True
                    observer._emit("newsletter.skill.replayed", f"Replayed a validated website skill for {target}", result="success")
                return locator
    return None


def _newsletter_agent_report_status(observer: "BrowserObserver") -> None:
    if observer._newsletter_agent_status_reported:
        return
    observer._newsletter_agent_status_reported = True
    settings = observer.newsletter_config.get("agent_settings")
    if not isinstance(settings, dict) or not settings.get("configured_enabled", settings.get("enabled", False)):
        if isinstance(settings, dict):
            detail = (
                "Newsletter Agent is disabled in workspace Settings "
                f"(enabled={bool(settings.get('configured_enabled', settings.get('enabled', False)))}, "
                f"provider_key_configured={bool(settings.get('api_key_configured', settings.get('api_key')))}, "
                f"live_execution_enabled={bool(settings.get('live_execution_enabled'))}, "
                f"learning_mode={bool(settings.get('learning_mode'))}, "
                f"shadow_mode={bool(settings.get('shadow_mode'))})"
            )
        else:
            detail = "Newsletter Agent is disabled in workspace Settings (runtime settings unavailable)"
        observer._emit("newsletter.agent.not_used", detail, result="skipped")
        return
    if not settings.get("api_key_configured", settings.get("api_key")):
        observer._emit("newsletter.agent.not_used", "Newsletter Agent is enabled but no provider key is configured", result="attention")
        return
    if settings.get("learning_mode") and settings.get("live_execution_enabled") and not settings.get("shadow_mode"):
        observer._emit("newsletter.agent.ready", "Newsletter Agent is enabled in learning mode; it may choose validated signup controls on the first run and record a redacted website skill", result="success")
    elif settings.get("shadow_mode"):
        observer._emit("newsletter.agent.ready", "Newsletter Agent is enabled in shadow mode; fallback decisions will not change the browser", result="success")
    elif settings.get("live_execution_enabled"):
        observer._emit("newsletter.agent.ready", "Newsletter Agent live fallback is enabled; it will run only after deterministic control lookup fails", result="success")
    else:
        observer._emit("newsletter.agent.not_used", "Newsletter Agent is configured but live execution is disabled", result="skipped")


def _newsletter_agent_not_needed(observer: "BrowserObserver", target: str) -> None:
    if observer._newsletter_agent_not_needed_reported:
        return
    settings = observer.newsletter_config.get("agent_settings")
    if not isinstance(settings, dict) or not settings.get("enabled"):
        return
    if target in observer._newsletter_agent_locators or observer._newsletter_skill_replayed:
        return
    observer._newsletter_agent_not_needed_reported = True
    observer._emit("newsletter.agent.not_needed", f"Deterministic {target} control was found; fallback agent was not invoked", result="skipped")


def _newsletter_plan_locator(page, observer: "BrowserObserver", target: str, context: dict[str, str] | None = None):
    settings = observer.newsletter_config.get("agent_settings")
    if not isinstance(settings, dict) or "agent_chain" not in settings:
        return None
    try:
        current_body = page.locator("body").inner_text(timeout=500)
        signature = hashlib.sha256(f"{page.url}\n{current_body[:5000]}".encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001
        signature = ""
    if signature and signature != getattr(observer, "_newsletter_agent_plan_signature", ""):
        observer._newsletter_agent_plan_loaded = False
        observer._newsletter_agent_plan = None
        observer._newsletter_agent_plan_signature = signature
    if not observer._newsletter_agent_plan_loaded:
        budget = max(0, min(int(settings.get("max_ai_calls", 4)), 4))
        remaining = budget - getattr(observer, "_newsletter_agent_plan_calls", 0) - getattr(observer, "_newsletter_step_ai_calls", 0)
        if remaining <= 0:
            return None
        observer._newsletter_agent_plan_loaded = True
        observer._emit("newsletter.agent.invoked", "requesting one bounded multi-action plan from the configured agent chain", result="started")
        plan = request_newsletter_action_plan(
            page,
            context=context,
            allowed_domains=_newsletter_config_lines(observer, "allowed_confirmation_domains"),
            agent_settings={**settings, "max_ai_calls": remaining},
        )
        observer._newsletter_agent_plan_calls += max(1, min(remaining, int(plan.get("_calls", remaining)) if isinstance(plan, dict) else remaining))
        observer._newsletter_agent_plan = plan
        if plan:
            observer._newsletter_agent_plan_meta = {key: plan.get(key) for key in ("_agent_name", "_priority", "_provider", "_model", "_calls", "_latency_ms")}
            observer._emit("newsletter.agent.plan_selected", f"Agent chain selected {plan.get('_agent_name')} ({plan.get('_model')}) after {plan.get('_calls')} call(s)", result="success", metadata=observer._newsletter_agent_plan_meta)
        else:
            observer._emit("newsletter.agent.no_decision", "Configured agent chain returned no application-validated signup plan", result="attention")
    plan = observer._newsletter_agent_plan
    if not isinstance(plan, dict):
        return None
    action_for_target = {"email_field": "FILL_EMAIL", "submit_button": "SUBMIT", "consent": "CHECK_CONSENT"}.get(target)
    if not action_for_target:
        return None
    selected = next((item for item in plan.get("actions", []) if isinstance(item, dict) and item.get("action") == action_for_target), None)
    if not selected:
        return None
    control_id = str(selected.get("control_id") or "")
    control_map = plan.get("_control_map") or {}
    control = control_map.get(control_id) if isinstance(control_map, dict) else None
    selector = str((control or {}).get("selector") or "") if isinstance(control, dict) else ""
    if not selector:
        observer._emit("newsletter.agent.no_decision", f"Agent plan did not resolve a current DOM selector for {target}", result="attention")
        return None
    locator = _newsletter_locator(page, selector)
    if locator is None:
        observer._emit("newsletter.agent.no_decision", f"Agent plan control {control_id} failed current DOM validation for {target}", result="attention")
        return None
    observer._newsletter_agent_locators[target] = locator
    observer._record_newsletter_skill_action({"kind": "locate", "target": target, "field_key": (context or {}).get("field_key"), "selector": selector, "control": (control or {}).get("control") if isinstance(control, dict) else None, "agent": observer._newsletter_agent_plan_meta})
    observer._emit("newsletter.agent.control_selected", f"Agent chain selected a verified {target} control ({observer._newsletter_agent_plan_meta.get('_agent_name')})", result="success", metadata=observer._newsletter_agent_plan_meta)
    return locator


def _newsletter_browser_use_locator(
    page,
    observer: "BrowserObserver",
    target: str,
    context: dict[str, str] | None = None,
    agent_settings: dict[str, object] | None = None,
):
    """Last automated resolver before a signup falls to manual review.

    Reached only when the deterministic lookup, a replayed learned skill, and
    the configured agent chain have all failed to produce a usable control.
    browser-use supplies the reasoning; this observer still performs every
    browser action, so the existing validation and audit paths are unchanged.
    """
    if observer._newsletter_browser_use_attempted:
        return None
    settings = {**(agent_settings or {}), **(observer.newsletter_config.get("browser_use_settings") or {})}
    if not settings.get("browser_use_enabled"):
        # Not configured for this workspace. Return before spending the one-shot
        # budget or emitting anything, so the audit trail only ever reports
        # browser-use attempts that really happened.
        return None
    if settings.get("shadow_mode"):
        # Shadow mode exists so an operator can watch the primary agent without
        # letting it act. The fallback must not run behind its back, and the
        # one-shot budget stays unspent for a later live run.
        return None
    observer._newsletter_browser_use_attempted = True
    from .newsletter_browser_agent import request_browser_use_suggestion

    observer._emit("newsletter.browser_use.invoked", f"requesting one bounded browser-use decision for {target}", result="started")
    suggestion = request_browser_use_suggestion(
        page,
        target,
        context=context,
        allowed_domains=_newsletter_config_lines(observer, "allowed_confirmation_domains"),
        agent_settings=settings,
    )
    if not suggestion:
        observer._emit("newsletter.browser_use.no_decision", f"browser-use returned no validated {target} control", result="attention")
        return None
    selector = str(suggestion.get("selector") or "")
    locator = _newsletter_locator(page, selector)
    if locator is None:
        observer._emit("newsletter.browser_use.no_decision", f"browser-use control failed current DOM validation for {target}", result="attention")
        return None
    observer._newsletter_agent_locators[target] = locator
    observer._record_newsletter_skill_action({
        "kind": "locate",
        "target": target,
        "field_key": (context or {}).get("field_key"),
        "selector": selector,
        "control": suggestion.get("control"),
        "agent": {"_agent_name": "browser-use"},
    })
    observer._emit(
        "newsletter.browser_use.control_selected",
        f"browser-use selected a verified {target} control (confidence {float(suggestion.get('confidence', 0.0)):.2f})",
        result="success",
    )
    return locator


def _newsletter_agent_locator(
    page,
    observer: "BrowserObserver",
    target: str,
    context: dict[str, str] | None = None,
):
    """Use a bounded provider in learning or enabled skill-relearning sessions."""
    execution_mode = getattr(observer, "_newsletter_execution_mode", AGENT_LEARNING if getattr(observer, "_newsletter_adaptive_learning", False) else DETERMINISTIC)
    agent_settings = observer.newsletter_config.get("agent_settings")
    if not isinstance(agent_settings, dict) or not agent_settings.get("enabled"):
        return None
    replay_relearning = execution_mode == SKILL_REPLAY and bool(agent_settings.get("automatic_relearning_enabled"))
    if not (allows_newsletter_agent(execution_mode) or replay_relearning):
        return None
    if "agent_chain" in agent_settings:
        locator = _newsletter_plan_locator(page, observer, target, context)
        if bool(agent_settings.get("shadow_mode")):
            if observer._newsletter_agent_plan:
                observer._emit("newsletter.agent.shadow_decision", "Shadow agent plan was validated but browser actions were not allowed", result="skipped", metadata=observer._newsletter_agent_plan_meta)
            return None
        if not bool(agent_settings.get("live_execution_enabled")):
            return None
        if locator is not None:
            return locator
        # The configured chain produced no usable control. Give the optional
        # browser-use resolver one attempt before manual review.
        return _newsletter_browser_use_locator(page, observer, target, context, agent_settings)
    shadow_mode = bool(agent_settings.get("shadow_mode"))
    live_execution = bool(agent_settings.get("live_execution_enabled")) and not shadow_mode
    if not shadow_mode and not live_execution:
        return None
    try:
        max_steps = max(1, min(int(agent_settings.get("max_steps", 12)), 24))
    except (TypeError, ValueError):
        max_steps = 12
    attempts = observer._newsletter_agent_attempts.get(target, 0)
    if attempts >= max_steps:
        return None
    observer._newsletter_agent_attempts[target] = attempts + 1
    invocation_reason = f"deterministic lookup found no usable {target} control"
    observer._emit("newsletter.agent.invoked", f"{invocation_reason}; requesting one bounded provider decision", result="started")
    suggestion = request_newsletter_control_suggestion(
        page,
        target,
        context=context,
        allowed_domains=_newsletter_config_lines(observer, "allowed_confirmation_domains"),
        agent_settings=agent_settings,
    )
    if not suggestion:
        observer._emit("newsletter.agent.no_decision", f"Provider returned no validated {target} control", result="attention")
        return _newsletter_browser_use_locator(page, observer, target, context, agent_settings)
    if not live_execution:
        observer._emit(
            "newsletter.agent.shadow_decision",
            f"Shadow agent suggested a verified {target} control (confidence {float(suggestion.get('confidence', 0.0)):.2f}); browser action was not allowed",
        )
        return None
    locator = _newsletter_locator(page, str(suggestion.get("selector") or ""))
    if locator is None:
        return None
    observer._newsletter_agent_locators[target] = locator
    observer._record_newsletter_skill_action({
        "kind": "locate",
        "target": target,
        "field_key": (context or {}).get("field_key"),
        "selector": str(suggestion.get("selector") or ""),
        "control": suggestion.get("control"),
    })
    observer._emit(
        "newsletter.agent.control_selected",
        f"Bounded agent selected a verified {target} control (confidence {float(suggestion.get('confidence', 0.0)):.2f})",
    )
    return locator


def _newsletter_selector_is_generic(selector: str) -> bool:
    normalized = re.sub(r"\s+", "", str(selector or "").lower()).replace('"', "").replace("'", "")
    if normalized in {
        "input", "input:visible", "select", "select:visible", "textarea", "textarea:visible",
        "input[type=text]", "input[type=text]:visible",
    }:
        return True
    return bool(re.fullmatch(r"(?:input|select|textarea)?\[(?:aria-label|placeholder|name|id)\](?::visible)?", normalized))


def _newsletter_field_locator(page, record: dict[str, object], observer: "BrowserObserver" | None = None):
    """Resolve a discovered field, tolerating dynamic IDs and embedded forms."""
    selector = str(record.get("selector") or "")
    field_key = str(record.get("field_key") or "").lower()
    field_type = str(record.get("field_type") or record.get("type") or "text").lower()
    autocomplete = str(record.get("autocomplete") or "").strip()
    label = str(record.get("label") or "").lower()
    placeholder = str(record.get("placeholder") or "").lower()
    tag = "select" if field_type == "select" else "textarea" if field_type == "textarea" else "input"
    if observer is not None:
        target = "email_field" if field_key == "email" else "field"
        if observer._newsletter_execution_mode == SKILL_REPLAY:
            skill_locator = _newsletter_skill_locator(page, observer, target, {"field_key": field_key})
            if skill_locator is not None:
                if field_key != "signup_password":
                    return skill_locator
                try:
                    if str(skill_locator.get_attribute("type") or "").lower() == "password":
                        return skill_locator
                except Exception:  # noqa: BLE001 - stale learned locator
                    pass
    # A control that cannot hold a typed value is a misidentification, not a
    # match, so it is skipped rather than filled. ``select`` and ``textarea``
    # carry their own tag and are always typed.
    expects_text = tag == "input" and field_type not in {"checkbox", "radio", "select", "textarea"}

    def _first_usable(candidates: list[str]):
        for candidate in candidates:
            resolved = _newsletter_locator(page, candidate)
            if resolved is None:
                continue
            if field_key == "signup_password":
                try:
                    if str(resolved.get_attribute("type") or "").lower() == "password":
                        return resolved
                except Exception:  # noqa: BLE001 - a stale form field is retried
                    pass
                continue
            if expects_text and not _newsletter_locator_type_is_text(resolved):
                continue
            return resolved
        return None

    # Discovery selectors are hints. Prefer stable semantic attributes from
    # the live control before falling back to a generated id or positional
    # selector captured by Scrapling.
    exact: list[str] = []
    for attribute in ("id", "name"):
        value = str(record.get(attribute) or "").strip()
        if value and re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value):
            exact.append(f'{tag}[{attribute}="{value}"]:visible')
    if autocomplete and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", autocomplete):
        exact.append(f'{tag}[autocomplete="{autocomplete}"]:visible')
    resolved = _first_usable(exact)
    if resolved is not None:
        return resolved

    # A form whose id and name were both rewritten by its framework still
    # carries a human label, so try that before falling back to a substring
    # match on an attribute, which is the weakest signal here.
    label_locator = _newsletter_label_locator(page, str(record.get("label") or ""))
    if label_locator is not None:
        try:
            if field_key == "signup_password":
                if str(label_locator.get_attribute("type") or "").lower() == "password":
                    return label_locator
            elif not expects_text or _newsletter_locator_type_is_text(label_locator):
                return label_locator
        except Exception:  # noqa: BLE001 - the label may re-render
            pass

    hints = attribute_field_hints(field_key)
    if not hints:
        raw_hints = re.findall(r"[a-z0-9]+", f"{field_key} {label} {placeholder}")
        hints = tuple(item for item in raw_hints if len(item) >= 3)[:4]
    loose: list[str] = []
    for hint in hints:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", hint):
            continue
        loose.append(f'{tag}[name*="{hint}" i]:visible')
        loose.append(f'{tag}[id*="{hint}" i]:visible')
        loose.append(f'{tag}[placeholder*="{hint}" i]:visible')
        loose.append(f'{tag}[aria-label*="{hint}" i]:visible')
    # A generic ``[type="text"]`` is deliberately not a candidate here; see
    # ``_NEWSLETTER_SPECIFIC_INPUT_TYPES``.
    if field_type in _NEWSLETTER_SPECIFIC_INPUT_TYPES and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", field_type):
        loose.append(f'{tag}[type="{field_type}"]:visible')
    if selector and not _newsletter_selector_is_generic(selector):
        loose.append(selector)
    resolved = _first_usable(loose)
    if resolved is not None:
        return resolved

    if field_key == "signup_password":
        return None
    if observer is not None and observer._newsletter_execution_mode == AGENT_LEARNING:
        target = "email_field" if field_key == "email" else "field"
        return _newsletter_agent_locator(page, observer, target, {"field_key": field_key, "field_type": field_type, "label": label})
    return None


def _newsletter_clickable(page, observer: "BrowserObserver"):
    if observer._newsletter_execution_mode == SKILL_REPLAY:
        learned = _newsletter_skill_locator(page, observer, "submit_button", {"control": "newsletter submit"})
        if learned is not None and _newsletter_is_submit_control(learned):
            return learned
    for selector in _newsletter_config_lines(observer, "submit_selector"):
        locator = _newsletter_locator(page, selector)
        if locator is not None and _newsletter_is_submit_control(locator):
            return locator
    for surface in _newsletter_surfaces(page, include_followup=observer._newsletter_submit_attempted):
        try:
            buttons = surface.locator("button:visible, input[type=submit]:visible, input[type=button]:visible, [role=button]:visible")
            for index in range(min(buttons.count(), 30)):
                button = buttons.nth(index)
                if not button.is_enabled(timeout=500):
                    continue
                label = _newsletter_normalize_label(" ".join((button.inner_text(timeout=500) or "", button.get_attribute("value") or "", button.get_attribute("aria-label") or "", button.get_attribute("title") or "")))
                if any(term in label for term in _NEWSLETTER_SUBMIT_LABELS):
                    return button
            # A public form can render an icon-only or text-in-shadow wrapper
            # whose inner text is not exposed to Playwright even though its
            # semantic submit type is. Restrict this fallback to a submit
            # control so navigation/search buttons are never activated.
            for selector in ("button[type=submit]:visible", "input[type=submit]:visible", "[role=button]:visible"):
                candidate = surface.locator(selector).first
                if candidate.count() and candidate.is_visible(timeout=500) and candidate.is_enabled(timeout=500):
                    return candidate
        except Exception:  # noqa: BLE001
            continue
    if observer._newsletter_execution_mode == AGENT_LEARNING:
        learned = _newsletter_agent_locator(page, observer, "submit_button", {"control": "newsletter submit"})
        return learned if learned is not None and _newsletter_is_submit_control(learned) else None
    return None


def _newsletter_is_submit_control(locator) -> bool:
    """Accept an approved submit selector only when its live control is clear."""
    try:
        input_type = str(locator.get_attribute("type") or "").strip().lower()
        if input_type == "submit":
            return True
        label = _newsletter_normalize_label(" ".join((
            str(locator.inner_text(timeout=400) or ""),
            str(locator.get_attribute("value") or ""),
            str(locator.get_attribute("aria-label") or ""),
            str(locator.get_attribute("title") or ""),
        )))
        return any(term in label for term in _NEWSLETTER_SUBMIT_LABELS)
    except Exception:  # noqa: BLE001 - stale profile controls are rescanned live
        return False


def _newsletter_attach_submission_listener(page, observer: "BrowserObserver") -> None:
    """Observe only bounded same-origin form response facts.

    Some newsletter providers acknowledge a successful submission through a
    same-origin POST/fetch while leaving the form shell visible and rendering
    no success phrase. We retain only the fact that an allowed submission-like
    endpoint returned a successful HTTP status; no request body or response
    body is read or persisted.
    """
    if observer._newsletter_response_listener_attached or not hasattr(page, "on"):
        return
    try:
        origin_host = (urlparse(page.url or observer.start_url).hostname or "").lower().rstrip(".")
    except Exception:
        origin_host = ""
    if not origin_host:
        return
    path_hints = ("subscribe", "signup", "newsletter", "form", "api")

    def on_response(response) -> None:
        if observer._newsletter_submission_response:
            return
        try:
            request = response.request
            method = str(getattr(request, "method", "") or "").upper()
            parsed = urlparse(str(getattr(response, "url", "") or ""))
            status = int(getattr(response, "status", 0) or 0)
            path = parsed.path.lower()
            if (
                method in {"POST", "PUT", "PATCH"}
                and parsed.hostname
                and parsed.hostname.lower().rstrip(".") == origin_host
                and 200 <= status < 400
                and any(hint in path for hint in path_hints)
            ):
                observer._newsletter_submission_response = True
        except Exception:  # noqa: BLE001 - response facts are best-effort
            return

    try:
        page.on("response", on_response)
        observer._newsletter_response_listener_attached = True
    except Exception:  # noqa: BLE001 - test doubles and detached pages
        return


def _newsletter_email_field(page, observer: "BrowserObserver"):
    for record in _newsletter_field_records(observer):
        if record.get("field_key") == "email":
            # Discovery frequently sees a generated ID (for example a form
            # instance number) that changes on the later browser load. Use
            # the same semantic/frame-aware resolver as other fields so a
            # stale exact selector cannot prevent typing the mailbox value.
            locator = _newsletter_field_locator(page, record, observer)
            if locator is not None:
                return locator
    for selector in _newsletter_config_lines(observer, "email_selector"):
        locator = _newsletter_locator(page, selector)
        if locator is not None and not _newsletter_selector_is_generic(selector) and _newsletter_locator_type_is_text(locator):
            return locator
    for selector in (
        "input[type=email]:visible",
        "input[autocomplete=email]:visible",
        "input[name*=email i]:visible",
        "input[name*=courriel i]:visible",
        "input[name*=correo i]:visible",
        "input[id*=email i]:visible",
        "input[id*=courriel i]:visible",
        "input[id*=correo i]:visible",
        "input[placeholder*=email i]:visible",
        "input[placeholder*=courriel i]:visible",
        "input[placeholder*=correo i]:visible",
        "input[aria-label*=email i]:visible",
        "input[aria-label*=courriel i]:visible",
        "input[aria-label*=correo i]:visible",
    ):
        locator = _newsletter_locator(page, selector)
        if locator is not None:
            return locator
    if observer._newsletter_execution_mode == AGENT_LEARNING:
        return _newsletter_agent_locator(page, observer, "email_field", {"field_key": "email", "field_type": "email"})
    return None


def _newsletter_step_started_at(observer: "BrowserObserver") -> float:
    """Return the clock the current signup step's wait budget is measured from.

    A reviewed entry step is a separate step with its own settle window. Every
    other budget keeps using the run's start, so the elapsed-time reporting is
    unchanged for single-step profiles.
    """
    return observer._newsletter_entry_at or observer._newsletter_started_at or time.monotonic()


def _newsletter_entry_control(page, observer: "BrowserObserver"):
    """Resolve the reviewed control that reveals the signup form, if any.

    Some newsletters open on a grid of cards and only render the email field
    after one is chosen. The operator approves that single pre-step in the
    profile; a profile without ``entry_selector`` (every previously discovered
    one) resolves to ``None`` and the run behaves exactly as before.

    The selector travels in the profile configuration, so every execution mode
    -- including ``SKILL_REPLAY`` -- replays the entry step without needing a
    second copy of it in the learned skill.
    """
    if observer._newsletter_entry_clicked:
        return None
    for selector in _newsletter_config_lines(observer, "entry_selector"):
        locator = _newsletter_locator(page, selector)
        if locator is not None:
            return locator
    return None


def _newsletter_advance_entry_step(page, observer: "BrowserObserver") -> bool:
    """Click the reviewed entry step once. Return True when it was attempted.

    One attempt per session: a page that never reveals the field must fall back
    to the ordinary timeout instead of clicking in a loop.
    """
    locator = _newsletter_entry_control(page, observer)
    if locator is None:
        return False
    # Guard before the action, exactly as the submit path does, so a detached
    # click or a second observer tick can never click the entry step twice.
    observer._newsletter_entry_clicked = True
    observer._newsletter_entry_at = time.monotonic()
    try:
        locator.click(timeout=1800, no_wait_after=True)
    except Exception as exc:  # noqa: BLE001
        _finish_newsletter(observer, "newsletter.signup.manual_review", f"The configured entry step could not be activated: {type(exc).__name__}", result="attention")
        return True
    observer._record_newsletter_skill_action({"kind": "click", "target": "entry_control"})
    observer._emit("newsletter.signup.entry_activated", "Activated the configured entry step to reach the signup form")
    return True


def _newsletter_body(page: "Page") -> str:
    chunks: list[str] = []
    for surface in _newsletter_surfaces(page):
        try:
            try:
                chunks.append(surface.title() or "")
            except Exception:
                pass
            chunks.append(surface.locator("body").inner_text(timeout=1000) or "")
        except Exception:  # noqa: BLE001
            continue
    return " ".join(chunks)[:8000].lower()


def _newsletter_attach_challenge_listener(page: "Page", observer: "BrowserObserver") -> None:
    if observer._newsletter_challenge_listener_attached or not hasattr(page, "on"):
        return

    def on_response(response) -> None:
        try:
            status = int(getattr(response, "status", 0) or 0)
            headers = getattr(response, "headers", {}) or {}
            header_names = [str(key).lower() for key in headers.keys()] if hasattr(headers, "keys") else []
            if status == 403:
                observer._newsletter_challenge_reason = "HTTP_403"
            elif status == 429:
                observer._newsletter_challenge_reason = "HTTP_429"
            elif "cf-mitigated" in header_names or any(name.startswith(("x-captcha", "x-challenge", "x-bot")) for name in header_names):
                # ``cf-mitigated`` is the header Cloudflare sets when it actually
                # intervened. Every other ``cf-`` header -- ``cf-ray``,
                # ``cf-cache-status``, ``cf-apo-via`` -- is ordinary proxy
                # metadata that every healthy response carries, so matching the
                # bare prefix flagged every Cloudflare-fronted site.
                observer._newsletter_challenge_reason = "ANTI_BOT_RESPONSE_HEADER"
        except Exception:  # noqa: BLE001 - response facts are best effort
            return

    try:
        page.on("response", on_response)
        observer._newsletter_challenge_listener_attached = True
    except Exception:  # noqa: BLE001 - detached pages are handled by the tick timeout
        return


def _newsletter_dom_challenge_reason(page: "Page") -> str | None:
    body = _newsletter_body(page)
    for marker in _NEWSLETTER_CHALLENGE_MARKERS:
        if marker in body:
            return f"DOM_{marker.upper().replace(' ', '_')}"
    # ``locator().count()`` is only meaningful for a real Playwright page.
    # Lightweight page doubles (and detached pages) may return a generic
    # locator for every selector, which must not turn a normal success page
    # into a phantom challenge iframe.
    if not hasattr(page, "frames"):
        return None
    for surface in _newsletter_surfaces(page):
        try:
            widget = surface.locator(_NEWSLETTER_CHALLENGE_IFRAME_SELECTOR)
            if widget.count():
                # Name the frame that matched. "CHALLENGE_IFRAME" alone left an
                # operator with a blocked run and no way to tell which embed
                # was blamed for it.
                try:
                    src = str(widget.first.get_attribute("src") or "")[:200]
                except Exception:  # noqa: BLE001 - the source is best effort
                    src = ""
                return f"CHALLENGE_IFRAME {src}".strip()
        except Exception:
            continue
    return None


def classify_newsletter_challenge(
    *,
    response_header: bool = False,
    visible_widget: bool = False,
    known_provider_marker: bool = False,
    visible_challenge_text: bool = False,
    challenge_url: bool = False,
    interaction_blocked: bool = False,
    functional_form: bool = False,
) -> str:
    """Classify anti-bot evidence without treating a lone header as proof."""
    if visible_challenge_text or known_provider_marker:
        return CONFIRMED_CHALLENGE
    # A rendered widget proves a challenge only once it has displaced the form.
    # Newsletter signup forms routinely embed Turnstile or reCAPTCHA while
    # leaving the email field and submit button rendered and usable, so a
    # widget sitting on a working form is a signal to watch, not a verdict.
    # Confirming it here ended deterministic runs before the email was ever
    # typed, failed the reviewed profile and walked the newsletter toward
    # AUTOMATION_BROKEN. An interstitial has no usable form left, so it still
    # confirms -- and it carries one of the copy markers handled just above.
    if visible_widget:
        return SUSPICIOUS_CHALLENGE if functional_form else CONFIRMED_CHALLENGE
    if challenge_url and interaction_blocked:
        return CONFIRMED_CHALLENGE
    if response_header:
        return SUSPICIOUS_CHALLENGE
    if functional_form:
        return NO_CHALLENGE
    return SUSPICIOUS_CHALLENGE if challenge_url or interaction_blocked else NO_CHALLENGE


def _newsletter_has_functional_signup_form(page: "Page") -> bool:
    """Return true only when the live page still exposes a normal form."""
    for surface in _newsletter_surfaces(page):
        try:
            email_count = surface.locator("input[type=email]:visible, input[autocomplete=email]:visible, input[name*=email i]:visible").count()
            submit_count = surface.locator("button[type=submit]:visible, input[type=submit]:visible, [role=button]:visible").count()
            if email_count and submit_count:
                return True
        except Exception:
            continue
    return False


def _newsletter_challenge_blocks_signup(page: "Page", observer: "BrowserObserver") -> bool:
    _newsletter_attach_challenge_listener(page, observer)
    dom_reason = _newsletter_dom_challenge_reason(page)
    reason = observer._newsletter_challenge_reason or dom_reason
    current_url = str(getattr(page, "url", "") or "").lower()
    functional_form = _newsletter_has_functional_signup_form(page)
    challenge_url = any(marker in current_url for marker in ("challenge", "captcha", "turnstile", "verify"))
    status = classify_newsletter_challenge(
        response_header=bool(observer._newsletter_challenge_reason),
        visible_widget=bool(dom_reason and dom_reason.startswith("CHALLENGE_IFRAME")),
        known_provider_marker=bool(dom_reason and dom_reason.startswith("DOM_CF_CHL")),
        visible_challenge_text=bool(dom_reason and dom_reason.startswith("DOM_")),
        challenge_url=challenge_url,
        interaction_blocked=bool(reason and not functional_form),
        functional_form=functional_form,
    )
    observer._newsletter_challenge_status = status
    if status == NO_CHALLENGE:
        observer._newsletter_challenge_since = None
        return False
    if status == SUSPICIOUS_CHALLENGE:
        observer._newsletter_challenge_since = None
        if reason and not observer._newsletter_challenge_suspicious_reported:
            observer._newsletter_challenge_suspicious_reported = True
            observer._emit("newsletter.signup.challenge_suspicious", f"Suspicious challenge signal observed ({reason}); the normal signup form remains available", result="attention")
        return False
    if observer._newsletter_challenge_since is None:
        observer._newsletter_challenge_since = time.monotonic()
    elapsed = time.monotonic() - observer._newsletter_challenge_since
    if elapsed >= _NEWSLETTER_CHALLENGE_WAIT_SECONDS and not observer._newsletter_manual_review_reported:
        # Report the evidence that produced the verdict. The response-header
        # signal is recorded on every Cloudflare-fronted page, so preferring it
        # over the DOM marker named the wrong cause whenever the DOM was what
        # actually confirmed the challenge.
        confirming = dom_reason or observer._newsletter_challenge_reason or reason
        _finish_newsletter(
            observer,
            "newsletter.signup.blocked_antibot",
            f"BLOCKED_ANTIBOT: public challenge remained after the bounded wait ({confirming})",
            result="attention",
        )
    return True


def _finish_newsletter(observer: "BrowserObserver", event: str, detail: str, result: str = "success") -> None:
    if event in {"newsletter.signup.manual_review", "newsletter.signup.blocked_antibot"}:
        if observer._newsletter_manual_review_reported:
            return
        observer._newsletter_manual_review_reported = True
    metadata = {"newsletter_skill": observer._newsletter_skill_payload()} if event == "newsletter.signup.submitted" and observer._newsletter_skill_payload() else None
    reuse_session = observer._emit(event, detail[:300], result=result, metadata=metadata)
    # A terminal signup result releases its browser slot. Standalone
    # anti-bot handoffs keep the visible browser available for an operator.
    bulk_task = bool(observer.newsletter_config.get("task_id"))
    if reuse_session:
        return
    if event != "newsletter.signup.blocked_antibot" or bulk_task:
        observer._stop.set()


def _handle_newsletter_signup_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    """Delegate signup work to the app-integrated newsletter runner."""
    from .newsletter_runner import run_signup_tick

    run_signup_tick(observer, page, host)



def _newsletter_pending_name_fields(page, observer: "BrowserObserver") -> list[str]:
    """Find visible, empty name fields the site revealed after the first submit.

    Name values come only from the account's approved/generated signup
    identity. Requiring an empty live control prevents a populated profile or
    success page from being treated as an unfinished name step.
    """
    name_keys = {"first_name", "last_name", "full_name"}
    pending: set[str] = set()
    for surface in _newsletter_surfaces(page, include_followup=True):
        for control in _newsletter_live_controls(surface, include_name_step=True):
            if control.get("hidden") or control.get("disabled"):
                continue
            field_key = semantic_field_key(control)
            if field_key not in name_keys or _newsletter_live_control_state(control) != "empty":
                continue
            locator = _newsletter_live_locator(surface, control)
            if locator is not None:
                pending.add(field_key)
    return sorted(pending)


def _newsletter_pending_additional_fields(page, observer: "BrowserObserver") -> list[str]:
    """Find one bounded post-submit step for identity, password or consent."""
    pending = set(_newsletter_pending_name_fields(page, observer))
    repeat_terms = (
        "confirm", "confirmation", "verify", "verification", "retype", "repeat",
        "again", "reenter", "re-enter", "confirmer", "confirmez", "verifier",
        "verifiez", "repetir", "confirmar", "verificar", "confirmacao", "wiederholen",
        "bestatigen", "conferma", "confermare", "herhalen",
    )
    email_terms = ("email", "e-mail", "mail", "courriel", "correo electronico", "direccion de correo", "endereco de email")
    for surface in _newsletter_surfaces(page, include_followup=True):
        for control in _newsletter_live_controls(surface, include_name_step=True):
            if control.get("hidden") or control.get("disabled") or _newsletter_live_control_state(control) != "empty":
                continue
            field_key = semantic_field_key(control)
            if field_key == "signup_password":
                pending.add(field_key)
                continue
            text = _newsletter_normalize_label(" ".join(
                str(control.get(key) or "")
                for key in ("name", "id", "aria_label", "title", "placeholder", "label")
            ))
            if field_key == "email" and any(term in text for term in email_terms) and any(term in text for term in repeat_terms):
                pending.add("email_confirmation")
                continue
            agreement_key = agreement_checkbox_key(control)
            if agreement_key is None and control.get("required") is True and field_key in {"consent", "terms"}:
                agreement_key = field_key
            if agreement_key is not None:
                pending.add(agreement_key)
    return sorted(pending)


def _newsletter_continue_name_step(observer: "BrowserObserver", page: "Page") -> bool:
    """Fill and submit one post-email details step, at most once per signup."""
    pending = _newsletter_pending_additional_fields(page, observer)
    if not pending:
        return False
    if observer._newsletter_followup_submit_attempted:
        return False

    observer._emit(
        "newsletter.signup.additional_fields_requested",
        "The site requested additional signup details after the initial submission",
    )
    ready, detail, _filled = _newsletter_fill_live_fields(page, observer)
    if not ready:
        _finish_newsletter(
            observer,
            "newsletter.signup.manual_review",
            f"The site requested additional details after the initial submit; {detail}",
            result="attention",
        )
        return True

    still_pending = _newsletter_pending_additional_fields(page, observer)
    if still_pending:
        _finish_newsletter(
            observer,
            "newsletter.signup.manual_review",
            "The site requested additional details that could not be verified after filling",
            result="attention",
        )
        return True

    submit = _newsletter_clickable(page, observer)
    if submit is None:
        from .newsletter_runner import _submit_followup_with_enter

        observer._newsletter_followup_submit_attempted = True
        _submit_followup_with_enter(observer, page, None)
        return True

    try:
        # The follow-up is a separate, bounded form step. Clear any response
        # fact from the first step so it cannot masquerade as final completion.
        observer._newsletter_submission_response = False
        observer._newsletter_followup_submit_attempted = True
        submit.click(timeout=1800, no_wait_after=True)
        observer._newsletter_last_submit_at = time.monotonic()
        observer._newsletter_verify_not_before = observer._newsletter_last_submit_at + 2.0
        observer._newsletter_last_url = page.url or observer._newsletter_last_url
        observer._record_newsletter_skill_action({"kind": "click", "target": "submit_button"})
        observer._emit(
            "newsletter.signup.followup_submitted",
            "Submitted the requested additional details once; awaiting the site's completion signal",
        )
    except Exception as exc:  # noqa: BLE001 - public forms can detach during navigation
        _finish_newsletter(
            observer,
            "newsletter.signup.manual_review",
            f"The follow-up signup control could not be activated: {type(exc).__name__}",
            result="attention",
        )
    return True


def _verify_newsletter_signup_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    """Delegate post-submit result classification to the app-integrated runner."""
    from .newsletter_runner import verify_signup_tick

    verify_signup_tick(observer, page, host)


_CONFIRMATION_SUCCESS_TERMS = (
    "subscription confirmed", "you are subscribed", "you’re subscribed", "you've been subscribed",
    "successfully confirmed", "confirmation successful", "thanks for confirming", "thank you for confirming",
    "inscription confirmée", "abonnement confirmé", "confirmation réussie", "suscripción confirmada",
    "suscripción confirmada", "inscrição confirmada", "bestätigung erfolgreich",
)
_CONFIRMATION_FAILURE_TERMS = (
    "link expired", "link is no longer valid", "invalid confirmation", "confirmation failed",
    "lien expiré", "lien invalide", "confirmation échouée", "enlace caducado", "enlace no válido",
)
_CONFIRMATION_LINK_TERMS = (
    "confirm", "verify", "activate", "validation", "confirmation", "opt-in", "double opt",
    "confirmer", "vérifier", "activer", "confirmer", "verificar", "activar", "bestätigen",
)


def _confirmation_query(observer: "BrowserObserver") -> str:
    subjects = _newsletter_config_lines(observer, "confirmation_subject_patterns")
    senders = _newsletter_config_lines(observer, "confirmation_sender_patterns")
    clauses: list[str] = []
    for subject in subjects[:3]:
        clean = " ".join(subject.replace('"', "").split())
        if clean:
            clauses.append(f'subject:"{clean}"')
    for sender in senders[:3]:
        clean = sender.replace('"', "").strip()
        if clean:
            clauses.append(f'from:{clean}')
    if not clauses:
        return "in:anywhere {subject:subscription subject:newsletter subject:subscribe}"
    return "in:anywhere (" + " OR ".join(clauses) + ")"


def _confirmation_open_message(page: "Page", observer: "BrowserObserver") -> bool:
    subjects = _newsletter_config_lines(observer, "confirmation_subject_patterns")
    senders = [item.lower() for item in _newsletter_config_lines(observer, "confirmation_sender_patterns")]
    try:
        rows = page.locator('[role="main"] tr.zA, [role="main"] tr, [role="main"] [role="row"], tr.zA, .zA').all()
    except Exception:  # noqa: BLE001
        return False
    for row in rows[:100]:
        try:
            if not row.is_visible(timeout=300):
                continue
            text = " ".join((row.inner_text(timeout=500) or "").split()).lower()
            subject_match = any(" ".join(item.split()).lower() in text for item in subjects if item.strip())
            sender_match = any(item in text for item in senders if item.strip())
            if (subjects or senders) and not subject_match and not sender_match:
                continue
            if not subjects and not senders and not (
                any(term in text for term in ("subscription", "subscribe", "newsletter", "opt-in"))
                and any(term in text for term in ("confirm", "verify", "activate", "complete", "finish"))
            ):
                continue
            targets = row.locator('td.a4W, td.y6, .bog, [role="link"], a').all()
            for target in targets:
                if target.is_visible(timeout=300):
                    target.click(timeout=2000)
                    return True
            row.click(timeout=2000)
            return True
        except Exception:  # noqa: BLE001 - Gmail virtualizes and re-renders rows
            continue
    return False


def _confirmation_allowed_host(hostname: str, observer: "BrowserObserver") -> bool:
    host = (hostname or "").lower().rstrip(".")
    for domain in _newsletter_config_lines(observer, "allowed_confirmation_domains"):
        allowed = domain.lower().removeprefix("https://").removeprefix("http://").split("/", 1)[0].rstrip(".")
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def _confirmation_link(page: "Page", observer: "BrowserObserver") -> str | None:
    anchors = []
    for root_selector in _MAIL_MESSAGE_BODY_ROOT_SELECTORS:
        try:
            for root in page.locator(root_selector).all():
                anchors.extend(root.locator('a[href]').all())
        except Exception:  # noqa: BLE001 - Gmail can replace a message body
            continue
    candidates: list[tuple[int, str]] = []
    for anchor in anchors[:250]:
        try:
            if not anchor.is_visible(timeout=200):
                continue
            href = str(anchor.get_attribute("href") or "").strip()
            parsed = urlparse(href)
            if parsed.scheme != "https" or not parsed.hostname or not _confirmation_allowed_host(parsed.hostname, observer):
                continue
            label = " ".join((anchor.inner_text(timeout=300) or "", anchor.get_attribute("aria-label") or "")).lower()
            if any(term in label for term in ("unsubscribe", "désabonner", "desabonner", "darse de baja", "opt-out")):
                continue
            if any(term in label for term in _CONFIRMATION_LINK_TERMS):
                candidates.append((1, href))
        except Exception:  # noqa: BLE001
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][1]


def _handle_newsletter_confirmation_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    if not observer._confirmation_started_at:
        observer._confirmation_started_at = time.monotonic()
        observer._emit("newsletter.confirmation.waiting", "Waiting for the expected confirmation message")
    observer._confirmation_tick_count += 1
    if observer._confirmation_tick_count > _MAIL_MAX_TICKS:
        _finish_newsletter(observer, "newsletter.confirmation.manual_review", "The confirmation message was not verified before the safe timeout", result="attention")
        return
    if host == "mail.google.com" and not observer._confirmation_search_submitted:
        field = _mail_search_input(page)
        if field is None:
            return
        try:
            field.fill(_confirmation_query(observer), timeout=1500)
            field.press("Enter", timeout=1500)
            observer._confirmation_search_submitted = True
        except Exception:  # noqa: BLE001
            return
        return
    if host == "mail.google.com" and not observer._confirmation_message_opened:
        observer._confirmation_wait_ticks += 1
        if _confirmation_open_message(page, observer):
            observer._confirmation_message_opened = True
            observer._confirmation_wait_ticks = 0
            observer._emit("newsletter.confirmation.found", "Found and opened a candidate confirmation message")
        elif observer._confirmation_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS * 3:
            _finish_newsletter(observer, "newsletter.confirmation.manual_review", "No unambiguous confirmation message matched the configured signals", result="attention")
        return
    if not observer._confirmation_link_opened:
        link = _confirmation_link(page, observer)
        if link is None:
            observer._confirmation_wait_ticks += 1
            if observer._confirmation_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS * 3:
                _finish_newsletter(observer, "newsletter.confirmation.manual_review", "No HTTPS confirmation link matched the allowlisted domains", result="attention")
            return
        observer._confirmation_link_url = link
        parsed = urlparse(link)
        try:
            page.goto(link, wait_until="commit", timeout=10000)
        except Exception:  # noqa: BLE001 - the final URL is checked below when possible
            pass
        final_url = page.url or ""
        final = urlparse(final_url)
        if final.scheme != "https" or not _confirmation_allowed_host(final.hostname or "", observer):
            _finish_newsletter(observer, "newsletter.confirmation.manual_review", "The confirmation link redirected outside the allowlisted HTTPS domains", result="attention")
            return
        observer._confirmation_link_opened = True
        observer._confirmation_wait_ticks = 0
        observer._emit("newsletter.confirmation.opened", f"Opened an allowlisted confirmation link on {parsed.hostname}")
        return
    observer._confirmation_wait_ticks += 1
    if not _confirmation_allowed_host(host, observer):
        _finish_newsletter(observer, "newsletter.confirmation.manual_review", "The confirmation page left the allowlisted domain", result="attention")
        return
    body = _newsletter_body(page)
    if any(term in body for term in _CONFIRMATION_FAILURE_TERMS):
        _finish_newsletter(observer, "newsletter.confirmation.failed", "The confirmation page reported an invalid or expired link", result="failure")
    elif any(term in body for term in _CONFIRMATION_SUCCESS_TERMS):
        _finish_newsletter(observer, "newsletter.confirmation.confirmed", "The confirmation page displayed a success confirmation", result="success")
    elif observer._confirmation_followup_steps < 2:
        ready, detail, filled = _mail_fill_confirmation_details(observer, page)
        if not ready:
            _finish_newsletter(observer, "newsletter.confirmation.manual_review", detail, result="attention")
            return
        if filled:
            observer._emit("newsletter.confirmation.details_filled", f"Filled {filled} approved confirmation field(s)")
        if _mail_click_landing_step(page):
            observer._confirmation_followup_steps += 1
            observer._confirmation_wait_ticks = 0
            observer._emit("newsletter.confirmation.followup_submitted", "Activated the confirmation form after verifying approved details")
            return
        if observer._confirmation_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS * 3:
            _finish_newsletter(observer, "newsletter.confirmation.manual_review", "The allowlisted page opened but did not display confirmation success evidence", result="attention")
    elif observer._confirmation_wait_ticks >= _MAIL_SEARCH_WAIT_TICKS * 3:
        _finish_newsletter(observer, "newsletter.confirmation.manual_review", "The allowlisted page opened but did not display confirmation success evidence", result="attention")


def _handle_alerts_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    """Create the server-selected topics one at a time in Google Alerts."""
    if host not in {"www.google.com", "google.com"} or not (urlparse(page.url or "").path.lower().rstrip("/") == "/alerts"):
        return
    observer._alerts_tick_count += 1
    if observer._alerts_tick_count > _ALERTS_MAX_TICKS:
        observer._emit("alerts.failed", "Google Alerts did not finish before the safe timeout", result="failure")
        observer._stop.set()
        return
    if not observer.alerts_topics:
        observer._emit("alerts.failed", "No Google Alerts topics were selected for this account", result="failure")
        observer._stop.set()
        return
    if observer._alerts_current_index >= len(observer.alerts_topics):
        observer._emit("alerts.completed", f"created {len(observer._alerts_created)} Google Alert(s)")
        observer._stop.set()
        return

    # Let Google's confirmation/rendering settle before entering the next
    # topic. This is non-blocking so cancellation and browser-close events
    # remain responsive during the pause.
    if time.monotonic() < getattr(observer, "_alerts_next_topic_at", 0.0):
        return

    topic = observer.alerts_topics[observer._alerts_current_index]
    typed_topic = _alerts_clean_topic(topic)
    if not typed_topic:
        observer._emit("alerts.failed", "Google Alerts topic was empty after removing quote characters", result="failure")
        observer._stop.set()
        return

    if observer._alerts_submit_signature != typed_topic:
        # Type only once. Google renders live results beneath the field while
        # the user is typing, so do not search for or click Create until the
        # complete five-second settling window has elapsed.
        if observer._alerts_typed_signature != typed_topic:
            field = _alerts_topic_input(page)
            if field is None:
                observer._alerts_wait_ticks += 1
                if observer._alerts_wait_ticks >= 15:
                    observer._emit("alerts.failed", "Google Alerts topic field was not available", result="failure")
                    observer._stop.set()
                return
            if not _alerts_type_topic(field, typed_topic):
                observer._alerts_wait_ticks += 1
                return
            observer._alerts_typed_signature = typed_topic
            observer._alerts_click_not_before = time.monotonic() + _ALERTS_AFTER_TYPE_DELAY_SECONDS
            observer._alerts_wait_ticks = 0
            observer._emit("alerts.topic_typed", f"typed Google Alert topic {typed_topic!r}; waiting before Create Alert")
            return

        if time.monotonic() < observer._alerts_click_not_before:
            return
        if not _alerts_click_create(page):
            observer._alerts_wait_ticks += 1
            if observer._alerts_wait_ticks >= 8:
                observer._emit("alerts.failed", f"Create Alert was not available for topic {typed_topic!r}", result="failure")
                observer._stop.set()
            return
        observer._alerts_submit_signature = typed_topic
        observer._alerts_verify_not_before = time.monotonic() + _ALERTS_AFTER_CLICK_DELAY_SECONDS
        observer._alerts_wait_ticks = 0
        observer._emit("alerts.topic_submitted", f"submitted Google Alert topic {typed_topic!r}")
        return

    if time.monotonic() < observer._alerts_verify_not_before:
        return

    observer._alerts_wait_ticks += 1
    try:
        visible = _visible_text(page).lower()
    except Exception:  # noqa: BLE001
        visible = ""
    if typed_topic.lower() in visible:
        observer._alerts_created.append(typed_topic)
        observer._alerts_current_index += 1
        observer._alerts_submit_signature = None
        observer._alerts_typed_signature = None
        observer._alerts_click_not_before = 0.0
        observer._alerts_verify_not_before = 0.0
        observer._alerts_wait_ticks = 0
        observer._alerts_next_topic_at = time.monotonic() + _ALERTS_INTER_TOPIC_DELAY_SECONDS
        observer._emit("alerts.topic_created", f"created Google Alert {observer._alerts_current_index} of {len(observer.alerts_topics)}")
        return
    if observer._alerts_wait_ticks >= 18:
        observer._emit("alerts.failed", f"Google Alerts did not verify topic {typed_topic!r}", result="failure")
        observer._stop.set()


_ACCOUNT_PREFERENCES_AVATAR_LABELS = (
    "change profile picture", "change profile photo", "profile picture", "profile photo",
    "changer la photo de profil", "photo de profil", "cambiar foto de perfil", "foto de perfil",
    "changer l'image de profil", "imagen de perfil",
)
_ACCOUNT_PREFERENCES_AVATAR_OPEN_LABELS = (
    "change profile picture", "change profile photo", "edit profile picture", "edit profile photo",
    "changer la photo de profil", "modifier la photo de profil", "cambiar foto de perfil", "editar foto de perfil",
    "changer l'image de profil", "modifier l'image de profil", "imagen de perfil",
    # In some localized rollouts the profile card itself is the only
    # actionable control; there is no separate Change/Edit label.
    "foto de perfil", "photo de profil",
)
_ACCOUNT_PREFERENCES_ILLUSTRATION_LABELS = (
    "browse illustrations", "see more illustrations", "explore illustrations",
    "parcourir les illustrations", "voir plus d'illustrations", "explorez les illustrations",
    "explorar ilustraciones", "ver más ilustraciones",
    "illustrationen durchsuchen", "illustrationen ansehen", "esplora illustrazioni",
    "explorar ilustrações", "illustraties verkennen", "przeglądaj ilustracje",
    "просмотреть иллюстрации", "イラストを閲覧", "일러스트 둘러보기", "浏览插图",
)
_ACCOUNT_PREFERENCES_SETTINGS_LABELS = (
    "settings", "quick settings", "paramètres", "réglages", "configuración", "ajustes",
)
_ACCOUNT_PREFERENCES_VIEW_ALL_LABELS = (
    "view all", "afficher tout", "voir tout", "ver todo", "mostrar todo",
)
_ACCOUNT_PREFERENCES_SAVE_LABELS = (
    "save", "done", "apply", "guardar", "listo", "aplicar", "enregistrer", "terminé", "appliquer",
)
_ACCOUNT_PREFERENCES_THEME_LABELS = ("theme", "thème", "tema", "thèmes", "temas")
_ACCOUNT_PREFERENCES_THEME_CHOICES = {
    "default": ("default", "par défaut", "predeterminado", "predeterminada"),
    "dark": ("dark", "sombre", "oscuro", "oscura"),
    "light": ("light", "clair", "claro", "clara"),
    "high_contrast": ("high contrast", "contraste élevé", "contraste eleve", "alto contraste"),
}
_ACCOUNT_PREFERENCES_PROFILE_PICKER_MARKERS = (
    r"browse illustrations|see more illustrations|explore illustrations|illustrations|ilustraciones|"
    r"start exploring|more to explore|search illustrations|add profile picture|"
    r"parcourir les illustrations|voir plus d'illustrations|explorez les illustrations|ajouter une photo de profil|"
    r"commencez à explorer|plus à explorer|rechercher des illustrations|"
    r"explorar ilustraciones|ver más ilustraciones|añadir foto de perfil|agregar foto de perfil|"
    r"agregar imagen de perfil|añadir imagen de perfil|comienza a explorar|más para explorar|busca ilustraciones|"
    r"change your profile picture|changez votre photo de profil|cambia tu foto de perfil|"
    r"cambiar tu foto de perfil|illustrationen durchsuchen|esplora illustrazioni|"
    r"explorar ilustrações|illustraties verkennen|przeglądaj ilustracje|"
    r"просмотреть иллюстрации|イラストを閲覧|일러스트 둘러보기|浏览插图"
)
_ACCOUNT_PREFERENCES_EXISTING_IMAGE_PICKER_MARKERS = (
    r"change your profile picture|change your profile photo|"
    r"changez votre photo de profil|changer votre photo de profil|changer la photo de profil|"
    r"cambia tu foto de perfil|cambiar tu foto de perfil|cambiar la foto de perfil|cambiar foto de perfil|"
    r"modifica la foto del profilo|cambia la foto del profilo|alterar sua foto de perfil|"
    r"wijzig je profielfoto|zmień zdjęcie profilowe|profilbild ändern|"
    r"изменить фотографию профиля|プロフィール写真を変更|프로필 사진 변경|更改头像"
)
_ACCOUNT_PREFERENCES_PROFILE_EDITOR_MARKERS = (
    r"quick crops|suggestions|crop and rotate|recortes rápidos|recortes rapidos|"
    r"sugerencias|recortar y rotar|recadrages rapides|recadrer et pivoter|"
    r"ajustes predeterminados"
)
_ACCOUNT_PREFERENCES_PROFILE_NEXT_LABELS = (
    "next", "continue", "siguiente", "continuar", "suivant", "continuer",
    "weiter", "avanti", "seguinte",
)
_ACCOUNT_PREFERENCES_PROFILE_SAVE_LABELS = (
    "save as profile picture", "save as profile photo", "use as profile picture",
    "enregistrer comme photo de profil", "utiliser comme photo de profil",
    "guardar como foto de perfil", "usar como foto de perfil",
    "speichern als profilbild", "als profilbild speichern",
    "salvar como foto de perfil", "usar como foto de perfil",
)
_ACCOUNT_PREFERENCES_THEME_PICKER_MARKERS = (
    r"pick your theme|choisir votre thème|elige tu tema|selecciona tu tema|"
    r"my photos|mes photos|mis fotos"
)


def _click_account_preference_control(page: "Page", labels: tuple[str, ...]) -> bool:
    wanted = tuple(label.casefold() for label in labels)
    # Action controls such as Gmail Settings and the profile-card opener live
    # in the current document. Picker contents are searched separately by
    # _visible_picker_root, because blindly evaluating this broad selector in
    # every Google iframe can keep a detached overlay alive.
    contexts = [page]
    try:
        # The Account picker/editor is commonly hosted in a cross-origin
        # iframe. Playwright can still address its rendered controls through
        # the frame, even though the main-document evaluate path cannot.
        contexts.extend(frame for frame in page.frames if frame is not page)
    except Exception:  # noqa: BLE001
        pass
    # Google frequently renders these controls as clickable divs (without a
    # button/link role) and changes the visible language per account.  Try the
    # live DOM first so we can use the rendered label and its nearest clickable
    # ancestor, then keep the locator scan as a fallback for test doubles and
    # older page variants.
    try:
        script = """
            (labels) => {
              const normalize = (value) => String(value || '')
                .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                .replace(/\\s+/g, ' ').trim().toLowerCase();
              const wanted = labels.map(normalize).filter(Boolean);
              const visible = (el) => {
                if (!el || !(el instanceof Element)) return false;
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                  && rect.width > 0 && rect.height > 0;
              };
              const controls = Array.from(document.querySelectorAll(
                'button, [role="button"], [role="link"], [role="menuitem"], a, [tabindex="0"], label, input[type="button"], input[type="submit"], [jsaction]'
              )).sort((a, b) => {
                const aText = String(a.innerText || a.getAttribute('aria-label') || '').length;
                const bText = String(b.innerText || b.getAttribute('aria-label') || '').length;
                return aText - bText;
              });
              for (const control of controls) {
                if (!visible(control)) continue;
                const values = [
                  control.innerText,
                  control.getAttribute('aria-label'),
                  control.getAttribute('title'),
                  control.getAttribute('data-tooltip')
                ].filter(Boolean).map(normalize);
                if (!values.some((value) => wanted.some((term) => value === term))) continue;
                control.click();
                return true;
              }
              // Some Google Account cards put the label in a plain div and
              // attach the handler to a surrounding card. Walk upward from
              // the matching text instead of treating the label click as
              // sufficient.
              const nodes = Array.from(document.querySelectorAll('*'));
              for (const node of nodes) {
                if (!visible(node)) continue;
                const values = [
                  node.innerText,
                  node.getAttribute('aria-label'),
                  node.getAttribute('title')
                ].filter(Boolean).map(normalize);
                if (!values.some((value) => wanted.some((term) => value === term))) continue;
                let current = node;
                for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
                  if (!visible(current)) continue;
                  const style = window.getComputedStyle(current);
                  const clickable = current.matches('button, [role="button"], [role="link"], a, [tabindex="0"], [jsaction], [onclick]')
                    || current.hasAttribute('jsname') || current.hasAttribute('jscontroller')
                    || current.hasAttribute('data-action') || style.cursor === 'pointer'
                    || depth === 0;
                  if (!clickable) continue;
                  current.click();
                  return true;
                }
              }
              return false;
            }
            """
        # Keep DOM-click evaluation scoped to the main document. In a Google
        # picker iframe, a matching text node can be a presentation wrapper;
        # use the frame's Playwright locator path below so the real button
        # receives the click event.
        for context in contexts[:1]:
            try:
                if context.evaluate(script, list(labels)):
                    return True
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    try:
        # Text can be rendered inside a non-semantic span while the click
        # handler lives on one of its parent cards. Try the exact visible
        # localized label and walk a few ancestors before the broad scan.
        for context in contexts:
            for label in labels:
                text_nodes = context.get_by_text(label, exact=True).all()
                if not text_nodes:
                    text_nodes = context.get_by_text(label).all()
                text_nodes.sort(key=lambda node: len((node.inner_text() or "").strip()))
                for text_node in text_nodes:
                    try:
                        if not text_node.is_visible():
                            continue
                        target = text_node
                        for depth in range(6):
                            try:
                                target.click(timeout=1500)
                                return True
                            except Exception:  # noqa: BLE001
                                target = target.locator("xpath=..")
                    except Exception:  # noqa: BLE001
                        continue
    except Exception:  # noqa: BLE001
        pass
    try:
        for context in contexts:
            for candidate in context.locator('button, [role="button"], [role="menuitem"], [role="link"], a, label, [tabindex="0"], input[type="button"], input[type="submit"]').all():
                try:
                    text = " ".join(filter(None, [candidate.inner_text(), candidate.get_attribute("aria-label"), candidate.get_attribute("title")])).strip().casefold()
                except Exception:  # noqa: BLE001
                    continue
                if not text or not any(term == text or term in text for term in wanted):
                    continue
                try:
                    candidate.click(timeout=1500)
                    return True
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        return False
    return False


def _visible_picker_root(page: "Page", marker_pattern: str) -> object | None:
    """Return the active Google picker dialog, including non-semantic variants."""
    # Google Account can render the picker inside a cross-origin iframe. A
    # screenshot shows that iframe as part of the page, but page.locator()
    # cannot see its DOM. Search the main document and every attached frame.
    contexts = [page]
    try:
        contexts.extend(frame for frame in page.frames if frame is not page)
    except Exception:  # noqa: BLE001
        pass
    fallback_roots: list[object] = []
    for context_index, context in enumerate(contexts):
        for selector in ('[role="dialog"]', '[aria-modal="true"]'):
            try:
                for root in context.locator(selector).all():
                    if root.is_visible() and re.search(marker_pattern, root.inner_text() or "", re.IGNORECASE):
                        return root
            except Exception:  # noqa: BLE001
                continue
        try:
            markers = context.get_by_text(re.compile(marker_pattern, re.IGNORECASE)).all()
            for marker in reversed(markers):
                root = marker
                for _ in range(7):
                    root = root.locator("xpath=..")
                    if not root.is_visible():
                        continue
                    if root.locator("img, [role=button], [role=gridcell], [tabindex='0']").count():
                        return root
        except Exception:  # noqa: BLE001
            pass
        # Some variants have no semantic dialog and split the marker across
        # spans. The body is a safe fallback scope because the selection
        # helpers apply strict size and label filters.
        try:
            body = context.locator("body")
            # Google picker overlays can be non-semantic and keep their
            # marker text split across hidden spans. The state machine marks
            # a tile as committed immediately after clicking it, so this
            # fallback is only used while that picker step is active.
            if body.is_visible() and re.search(marker_pattern, body.inner_text() or "", re.IGNORECASE):
                candidate_count = body.locator(
                    'button, [role="gridcell"], [role="button"], [role="radio"], [role="option"], '
                    '[tabindex="0"], [jsaction], [onclick], [data-theme-id], [data-tooltip], '
                    '[style*="background"], img'
                ).count()
                if candidate_count:
                    fallback_roots.append((candidate_count, context_index, body))
        except Exception:  # noqa: BLE001
            continue
    if fallback_roots:
        # Prefer the picker surface with the richest set of actual choice
        # candidates over a hidden frame that only retains marker text.
        return max(fallback_roots, key=lambda item: (item[0], -item[1]))[2]
    return None


def _profile_picker_is_existing_image_menu(root: object) -> bool:
    """Identify the menu shown when Google already has a profile image.

    That menu contains an illustration entry followed by upload and camera
    actions.  It must be handled before the generic tile picker because the
    large current-image surface can otherwise be mistaken for a tile and the
    camera row can be selected by a broad fallback.
    """
    try:
        text = str(root.inner_text() or "")
    except Exception:  # noqa: BLE001 - detached overlays are expected
        return False
    normalized = (
        unicodedata.normalize("NFD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )
    has_illustrations = any(
        term in normalized
        for term in (
            "browse illustrations",
            "see more illustrations",
            "explore illustrations",
            "parcourir les illustrations",
            "voir plus d'illustrations",
            "explorez les illustrations",
            "explorar ilustraciones",
            "ver mas ilustraciones",
            "illustrationen durchsuchen",
            "esplora illustrazioni",
            "explorar ilustracoes",
        )
    )
    has_upload = any(
        term in normalized
        for term in (
            "upload from device",
            "download from device",
            "telecharger depuis l'appareil",
            "subir desde el dispositivo",
            "télécharger depuis l'appareil",
            "carica dal dispositivo",
            "enviar do dispositivo",
        )
    )
    has_camera = any(
        term in normalized
        for term in (
            "take a picture",
            "take a photo",
            "prenez une photo",
            "prendre une photo",
            "tomar una foto",
            "toma una foto",
            "scatta una foto",
            "tirar uma foto",
        )
    )
    return has_illustrations and has_upload and has_camera


def _click_profile_illustration_entry(page: "Page", root: object) -> bool:
    """Click the localized first menu option, never the upload/camera rows."""
    labels = tuple(label.casefold() for label in _ACCOUNT_PREFERENCES_ILLUSTRATION_LABELS)
    # Prefer the exact visible label before inspecting jsaction wrappers. In
    # the current Google Account menu the wrapper spans the whole dialog, so
    # its center overlaps the image/camera area even though its text is the
    # illustration row.
    try:
        text_targets = []
        for label in _ACCOUNT_PREFERENCES_ILLUSTRATION_LABELS:
            for text_node in root.get_by_text(label).all():
                if not text_node.is_visible():
                    continue
                box = text_node.bounding_box()
                if not box or box["width"] <= 0 or box["height"] <= 0:
                    continue
                text_targets.append((box["width"] * box["height"], text_node, box))
        if text_targets:
            _area, text_node, box = min(text_targets, key=lambda item: item[0])
            if hasattr(page, "mouse"):
                page.mouse.click(
                    box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2,
                )
            else:
                text_node.click(timeout=2000, force=True)
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        controls = root.locator(
            'button, [role="button"], [role="menuitem"], [role="option"], '
            '[tabindex="0"], [jsaction], a'
        ).all()
    except Exception:  # noqa: BLE001
        controls = []
    for control in controls:
        try:
            if not control.is_visible():
                continue
            text = " ".join(
                filter(
                    None,
                    [
                        control.inner_text(),
                        control.get_attribute("aria-label"),
                        control.get_attribute("title"),
                    ],
                )
            ).strip().casefold()
            if not text or not any(label == text or label in text for label in labels):
                continue
            # Google sometimes attaches the handler to a plain DIV whose
            # jsaction is activated only by a real pointer event. Prefer the
            # smallest visible text descendant: the outer action wrapper can
            # contain an accessibility clone and clicking its center can land
            # on the wrong part of the menu.
            target = control
            target_area = float("inf")
            try:
                for descendant in control.locator("*").all():
                    if not descendant.is_visible():
                        continue
                    descendant_text = " ".join(
                        filter(None, [descendant.inner_text(), descendant.get_attribute("aria-label")])
                    ).strip().casefold()
                    if not descendant_text or not any(label in descendant_text for label in labels):
                        continue
                    descendant_box = descendant.bounding_box()
                    if not descendant_box:
                        continue
                    area = descendant_box["width"] * descendant_box["height"]
                    if 0 < area < target_area:
                        target = descendant
                        target_area = area
            except Exception:  # noqa: BLE001
                pass
            box = target.bounding_box()
            if box and hasattr(page, "mouse"):
                page.mouse.click(
                    box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2,
                )
            else:
                target.click(timeout=2000)
            return True
        except Exception:  # noqa: BLE001
            continue
    # A few Google locales expose the row as a plain div and put the click
    # handler on an overlay, so the semantic-control pass above finds no
    # usable target.  Click the exact localized label itself, bounded to the
    # picker root; this cannot reach the upload or camera rows.
    try:
        for label in _ACCOUNT_PREFERENCES_ILLUSTRATION_LABELS:
            for text_node in root.get_by_text(label, exact=True).all():
                if not text_node.is_visible():
                    continue
                box = text_node.bounding_box()
                if not box or box["width"] <= 0 or box["height"] <= 0:
                    continue
                try:
                    text_node.click(timeout=2000, force=True)
                except Exception:  # noqa: BLE001
                    if not hasattr(page, "mouse"):
                        continue
                    page.mouse.click(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _account_preferences_debug_controls(page: "Page") -> list[str]:
    """Return bounded, non-sensitive labels for diagnosing a missing control."""
    try:
        values = page.evaluate(
            """
            () => Array.from(document.querySelectorAll(
              'button, [role="button"], [role="link"], [role="menuitem"], a, [tabindex="0"], label, [jsaction]'
            )).filter((el) => {
              const rect = el.getBoundingClientRect();
              const style = window.getComputedStyle(el);
              return style.display !== 'none' && style.visibility !== 'hidden'
                && rect.width > 0 && rect.height > 0;
            }).map((el) => [
              el.innerText,
              el.getAttribute('aria-label'),
              el.getAttribute('title'),
              el.getAttribute('data-tooltip')
            ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim())
            .filter((value) => /profile|photo|picture|illustr|settings|param|config|avatar|theme|tema|thème|view all|afficher|ver todo|save|guardar|enregistrer/i.test(value))
            .slice(0, 40)
            """,
        )
        return [str(value)[:180] for value in values if value]
    except Exception:  # noqa: BLE001
        return []


def _click_random_profile_illustration(page: "Page") -> bool:
    """Select one visible illustration from Google's profile-picture picker."""
    candidates = []
    try:
        # Never inspect the page-wide image set: the account header also has
        # visible avatars and navigation icons. The picker is the only safe
        # scope for selecting an illustration.
        root = _visible_picker_root(page, _ACCOUNT_PREFERENCES_PROFILE_PICKER_MARKERS)
        if root is None:
            return False
        # When an account already has an image, Google shows a menu with
        # "Explore illustrations", "Upload", and "Take a photo". Its large
        # current-avatar surface can look like a selectable tile, and the
        # camera row can then be chosen accidentally. Never run tile selection
        # until the localized Explore-illustrations entry has opened the grid.
        if _profile_picker_is_existing_image_menu(root):
            logger.info("account preferences profile picker is the existing-image menu; refusing camera/upload tile fallback")
            return False
        # Restrict clicks to image-backed tiles or explicit grid choices.
        # Broad tabindex/jsaction selectors can match carousel controls that
        # only advance suggestions without selecting a profile image.
        locators = root.locator('img, [role="gridcell"], [role="radio"], [role="option"], [data-theme-id]').all()
        for candidate in locators:
            try:
                box = candidate.bounding_box()
                # The profile picker tiles are large illustrations. Requiring
                # a substantial tile prevents the page avatar and small
                # navigation icons from being mistaken for a choice when the
                # picker has no semantic dialog container.
                if not box or box["width"] < 70 or box["height"] < 70:
                    continue
                text = " ".join(filter(None, [candidate.get_attribute("alt"), candidate.get_attribute("aria-label"), candidate.get_attribute("title")])).casefold()
                if any(term in text for term in ("close", "cancel", "upload", "camera", "take a picture", "browse illustrations", "see more illustrations", "next", "back", "previous", "carousel")):
                    continue
                # An image is often only the visual child of Google's
                # clickable tile. Prefer the tile ancestor when present.
                clickable = candidate.locator(
                    "xpath=ancestor-or-self::*[@role='button' or @role='gridcell' or @role='radio' or @role='option' or @data-theme-id or self::button][1]"
                )
                if clickable.count():
                    candidate = clickable
                candidates.append(candidate)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return False
    if not candidates:
        logger.warning("account preferences profile picker exposed no eligible illustration tiles")
        return False
    try:
        candidate = random.choice(candidates)
        try:
            candidate.click(timeout=2000, force=True)
        except TypeError:
            candidate.click(timeout=2000)
        return True
    except Exception:  # noqa: BLE001
        try:
            candidate.dispatch_event("click")
            return True
        except Exception:  # noqa: BLE001
            return False


def _click_random_theme_card(page: "Page") -> bool:
    """Select one visible theme card in Gmail's View all theme dialog."""
    candidates = []
    try:
        root = _visible_picker_root(page, _ACCOUNT_PREFERENCES_THEME_PICKER_MARKERS)
        if root is None:
            return False
        # Gmail renders theme tiles differently across rollouts: some are
        # gridcells, while others are plain clickable divs with a data
        # attribute, inline background image, or jsaction. Include those
        # explicit interactive forms without scanning every page element.
        locators = root.locator(
            'button, [role="gridcell"], [role="button"], [role="radio"], [role="option"], '
            '[tabindex="0"], [jsaction], [onclick], [data-theme-id], [data-tooltip], '
            '[style*="background"], img'
        ).all()
        for candidate in locators:
            try:
                box = candidate.bounding_box()
                if not box or box["width"] < 70 or box["height"] < 45:
                    continue
                text = " ".join(filter(None, [candidate.inner_text(), candidate.get_attribute("aria-label"), candidate.get_attribute("title")])).casefold()
                if any(term in text for term in ("save", "cancel", "my photos", "mes photos", "mis fotos", "close")):
                    continue
                clickable = candidate.locator(
                    "xpath=ancestor-or-self::*[@role='button' or @role='gridcell' or @role='radio' or @role='option' or @tabindex='0' or @jsaction or @onclick'][1]"
                )
                if clickable.count():
                    candidate = clickable
                candidates.append(candidate)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return False
    if not candidates:
        # Gmail can render the View all grid as a canvas-like visual surface
        # with only the dialog heading and Save control exposed to the DOM.
        # Anchor a bounded click row to the visible localized heading instead
        # of guessing against the whole page.
        try:
            markers = root.get_by_text(
                re.compile(
                    r"pick your theme|choisir votre thème|elige tu tema|selecciona tu tema",
                    re.IGNORECASE,
                )
            ).all()
            marker_boxes = []
            for marker in markers:
                try:
                    if marker.is_visible():
                        box = marker.bounding_box()
                        if box and box["width"] > 0 and box["height"] > 0:
                            marker_boxes.append(box)
                except Exception:  # noqa: BLE001
                    continue
            if marker_boxes and hasattr(page, "mouse"):
                marker_box = min(marker_boxes, key=lambda box: box["width"] * box["height"])
                offsets = [(95, 110), (290, 110), (485, 110), (680, 110)]
                random.shuffle(offsets)
                for offset_x, offset_y in offsets:
                    x = marker_box["x"] + offset_x
                    y = marker_box["y"] + offset_y
                    try:
                        page.mouse.click(x, y)
                        logger.info("account preferences theme picker used bounded theme tile fallback")
                        return True
                    except Exception:  # noqa: BLE001
                        continue
        except Exception:  # noqa: BLE001
            pass
        return False
    try:
        random.choice(candidates).click(timeout=2000)
        return True
    except Exception:  # noqa: BLE001
        return False


def _handle_account_preferences_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    """Change the profile picture and Gmail theme through Google's own UI."""
    observer._preferences_tick_count += 1
    if observer._preferences_tick_count > 180:
        observer._emit("account_preferences.failed", "Google account preferences did not reach a verified completion state", result="failure")
        observer._stop.set()
        return
    config = observer.account_preferences
    theme = str(config.get("gmail_theme") or "default")
    change_avatar = bool(config.get("change_avatar", True))
    change_theme = bool(config.get("change_theme", True))
    random_theme = bool(config.get("random_theme", True))
    visible = _visible_text(page).casefold()

    if change_avatar and not observer._preferences_avatar_saved:
        if host != "myaccount.google.com":
            try:
                page.goto("https://myaccount.google.com/personal-info", wait_until="domcontentloaded", timeout=10000)
            except Exception:  # noqa: BLE001
                pass
            return
        if not observer._preferences_avatar_clicked:
            picker_open = _visible_picker_root(page, _ACCOUNT_PREFERENCES_PROFILE_PICKER_MARKERS) is not None
            picker_root = _visible_picker_root(page, _ACCOUNT_PREFERENCES_PROFILE_PICKER_MARKERS)
            existing_image_picker_open = bool(
                picker_root is not None and _profile_picker_is_existing_image_menu(picker_root)
            ) or re.search(_ACCOUNT_PREFERENCES_EXISTING_IMAGE_PICKER_MARKERS, visible, re.IGNORECASE) is not None
            if existing_image_picker_open:
                # Accounts with an existing image open a different first
                # menu. Select its localized "Explore illustrations" row
                # before looking for the illustration grid.
                if picker_root is not None and _click_profile_illustration_entry(page, picker_root):
                    observer._preferences_settle_ticks = 0
                    logger.info("session %s: opened localized profile illustration chooser", observer.session_id)
                    return
                if _click_account_preference_control(page, _ACCOUNT_PREFERENCES_ILLUSTRATION_LABELS):
                    observer._preferences_settle_ticks = 0
                    logger.info("session %s: opened localized profile illustration chooser", observer.session_id)
                    return
                observer._preferences_settle_ticks += 1
                if observer._preferences_settle_ticks >= 10:
                    observer._emit("account_preferences.failed", "Google's profile picture menu did not expose its illustration chooser", result="failure")
                    observer._stop.set()
                return
            if picker_open and _click_random_profile_illustration(page):
                observer._preferences_avatar_clicked = True
                observer._preferences_avatar_selected = False
                # A successful pointer click is not proof of selection: the
                # illustration carousel can move without changing the image.
                # Confirm that Google's crop/editor view opens before we mark
                # the choice selected or advance to another action.
                observer._preferences_avatar_saved = False
                observer._preferences_avatar_editor_next_clicked = False
                observer._preferences_avatar_commit_clicked = False
                observer._preferences_settle_ticks = 0
                logger.info("session %s: clicked a Google illustration tile; waiting for editor confirmation", observer.session_id)
                return
            if picker_open and _click_account_preference_control(page, _ACCOUNT_PREFERENCES_ILLUSTRATION_LABELS):
                observer._preferences_settle_ticks = 0
                return
            if picker_open:
                # Stay inside the picker instead of clicking the underlying
                # profile control again. Reopening it can advance carousel
                # suggestions and makes the run appear to select repeatedly.
                observer._preferences_settle_ticks += 1
                if observer._preferences_settle_ticks >= 10:
                    observer._emit("account_preferences.failed", "Google's illustration picker did not expose a selectable image tile", result="failure")
                    observer._stop.set()
                return
            # Do not use the generic "Profile picture" accessibility node
            # here: Google exposes it alongside the real action, and clicking
            # the generic node leaves the page unchanged. Target the explicit
            # change/edit action instead.
            if _click_account_preference_control(page, _ACCOUNT_PREFERENCES_AVATAR_OPEN_LABELS):
                if observer._preferences_tick_count <= 3:
                    try:
                        page.screenshot(path=f"/tmp/account-preferences-{observer.session_id}.png")
                    except Exception:  # noqa: BLE001 - diagnostic only
                        pass
                    logger.info(
                        "session %s: clicked Google profile picture control; waiting for picker; relevant controls=%s",
                        observer.session_id,
                        _account_preferences_debug_controls(page),
                    )
                return
            if observer._preferences_tick_count in {3, 10}:
                logger.warning(
                    "session %s: account preferences profile control not found; visible relevant controls=%s",
                    observer.session_id,
                    _account_preferences_debug_controls(page),
                )
            return
        editor_open = (
            _visible_picker_root(page, _ACCOUNT_PREFERENCES_PROFILE_EDITOR_MARKERS) is not None
            or re.search(_ACCOUNT_PREFERENCES_PROFILE_EDITOR_MARKERS, visible, re.IGNORECASE) is not None
        )
        if not observer._preferences_avatar_selected:
            if editor_open:
                observer._preferences_avatar_selected = True
                observer._preferences_settle_ticks = 0
                observer._emit("account_preferences.avatar_selected", "Google opened the editor for the selected illustration")
                return
            observer._preferences_settle_ticks += 1
            if observer._preferences_settle_ticks < 20:
                return
            observer._emit("account_preferences.failed", "Google did not open the illustration editor; no profile image was confirmed selected", result="failure")
            observer._stop.set()
            return
        if not observer._preferences_avatar_editor_next_clicked:
            if _click_account_preference_control(page, _ACCOUNT_PREFERENCES_PROFILE_NEXT_LABELS):
                observer._preferences_avatar_editor_next_clicked = True
                observer._preferences_settle_ticks = 0
                logger.info("session %s: advanced Google profile image editor", observer.session_id)
                return
            # The editor can be rendered in a cross-origin surface whose
            # marker text is not exposed to the top-level accessibility tree.
            # Keep probing only the localized Next control for a bounded
            # window; do not navigate away or report success meanwhile.
            observer._preferences_settle_ticks += 1
            if observer._preferences_settle_ticks < 20:
                return
            observer._emit("account_preferences.failed", "Google profile image editor did not expose its localized Next control", result="failure")
            observer._stop.set()
            return
        if editor_open and observer._preferences_avatar_editor_next_clicked:
            # Some locales show a second confirmation button after Next;
            # clicking a localized Save/Done is safe only inside the editor.
            if not observer._preferences_avatar_commit_clicked and _click_account_preference_control(page, _ACCOUNT_PREFERENCES_PROFILE_SAVE_LABELS):
                observer._preferences_avatar_commit_clicked = True
                observer._preferences_settle_ticks = 0
                return
            if observer._preferences_avatar_commit_clicked:
                observer._preferences_settle_ticks += 1
                if observer._preferences_settle_ticks < 5:
                    return
                observer._preferences_avatar_saved = True
                observer._preferences_settle_ticks = 0
                observer._emit("account_preferences.avatar_saved", "saved the selected Google illustration")
                return
            if _click_account_preference_control(page, _ACCOUNT_PREFERENCES_SAVE_LABELS):
                observer._preferences_avatar_commit_clicked = True
                observer._preferences_settle_ticks = 0
                return
            observer._preferences_settle_ticks += 1
            if observer._preferences_settle_ticks < 20:
                return
            observer._emit("account_preferences.failed", "Google profile image editor did not expose its localized save control", result="failure")
            observer._stop.set()
            return
        if observer._preferences_avatar_editor_next_clicked and not editor_open:
            observer._preferences_settle_ticks += 1
            if observer._preferences_settle_ticks < 8:
                return
            observer._preferences_avatar_saved = True
            observer._preferences_settle_ticks = 0
            observer._emit("account_preferences.avatar_saved", "saved the selected Google illustration")
            return
        if _click_account_preference_control(page, _ACCOUNT_PREFERENCES_SAVE_LABELS):
            observer._preferences_avatar_saved = True
            observer._preferences_settle_ticks = 0
            observer._emit("account_preferences.avatar_saved", "saved the selected Google illustration")
            return
        observer._preferences_settle_ticks += 1
        if observer._preferences_settle_ticks < 8:
            return
        observer._preferences_avatar_saved = True

    if not change_theme:
        observer._emit("account_preferences.completed", "applied the selected account image preference")
        observer._stop.set()
        return
    if host != "mail.google.com":
        try:
            page.goto("https://mail.google.com/mail/u/0/#inbox", wait_until="domcontentloaded", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        return
    if not observer._preferences_theme_settings_opened:
        if _click_account_preference_control(page, _ACCOUNT_PREFERENCES_SETTINGS_LABELS):
            observer._preferences_theme_settings_opened = True
            observer._preferences_settle_ticks = 0
            logger.info("session %s: opened Gmail Quick settings for account preferences", observer.session_id)
            return
        if observer._preferences_tick_count in {3, 10, 30, 60}:
            logger.warning(
                "session %s: account preferences Gmail settings control not found; visible relevant controls=%s",
                observer.session_id,
                _account_preferences_debug_controls(page),
            )
        return
    if not observer._preferences_theme_menu_opened:
        if _click_account_preference_control(page, _ACCOUNT_PREFERENCES_THEME_LABELS):
            observer._preferences_theme_menu_opened = True
            observer._preferences_settle_ticks = 0
            logger.info("session %s: opened Gmail Theme menu for account preferences", observer.session_id)
            return
        if observer._preferences_tick_count in {3, 10, 30, 60}:
            logger.warning(
                "session %s: account preferences Theme control not found; visible relevant controls=%s",
                observer.session_id,
                _account_preferences_debug_controls(page),
            )
        return
    if not observer._preferences_theme_view_all:
        if _click_account_preference_control(page, _ACCOUNT_PREFERENCES_VIEW_ALL_LABELS):
            observer._preferences_theme_view_all = True
            observer._preferences_settle_ticks = 0
            logger.info("session %s: opened Gmail View all themes for account preferences", observer.session_id)
            return
        if observer._preferences_tick_count in {3, 10, 30, 60}:
            logger.warning(
                "session %s: account preferences View all control not found; visible relevant controls=%s",
                observer.session_id,
                _account_preferences_debug_controls(page),
            )
        return
    if not observer._preferences_theme_clicked:
        selected = _click_random_theme_card(page) if random_theme else _click_account_preference_control(page, _ACCOUNT_PREFERENCES_THEME_CHOICES.get(theme, _ACCOUNT_PREFERENCES_THEME_CHOICES["default"]))
        if selected:
            observer._preferences_theme_clicked = True
            observer._preferences_settle_ticks = 0
            observer._emit("account_preferences.theme_selected", "selected a random Gmail theme" if random_theme else f"selected the {theme} Gmail theme")
        elif observer._preferences_tick_count in {3, 10, 30, 60}:
            logger.warning(
                "session %s: account preferences theme picker exposed no eligible theme cards; visible relevant controls=%s",
                observer.session_id,
                _account_preferences_debug_controls(page),
            )
        return
    if not observer._preferences_theme_saved:
        if _click_account_preference_control(page, _ACCOUNT_PREFERENCES_SAVE_LABELS):
            observer._preferences_theme_saved = True
            observer._preferences_settle_ticks = 0
            observer._emit("account_preferences.theme_saved", "saved the selected Gmail theme")
            return
        observer._preferences_settle_ticks += 1
        if observer._preferences_tick_count in {3, 10, 30, 60}:
            logger.warning(
                "session %s: account preferences theme Save control not found; visible relevant controls=%s",
                observer.session_id,
                _account_preferences_debug_controls(page),
            )
        return
    observer._preferences_settle_ticks += 1
    if observer._preferences_settle_ticks >= 3:
        observer._emit("account_preferences.completed", "applied the Google illustration and Gmail theme preferences")
        observer._stop.set()


def _handle_labels_markers_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    """Enable importance markers and show Gmail's Spam system label.

    Gmail settings are rendered as dynamic tables and vary by locale. The
    observer uses the visible section/option text and the semantic row
    relationship instead of brittle coordinates. Changes are saved before
    moving to the next settings page, and success is reported only after both
    settings are verified in the authenticated profile.
    """
    observer._labels_markers_tick_count = getattr(observer, "_labels_markers_tick_count", 0) + 1
    if observer._labels_markers_tick_count > 120:
        observer._emit("labels_markers.failed", "Gmail settings did not reach a verified completion state", result="failure")
        observer._stop.set()
        return
    if host != "mail.google.com":
        return
    url = page.url or ""
    fragment = urlparse(url).fragment.lower()
    if not getattr(observer, "_markers_verified", False):
        if "settings/inbox" not in fragment:
            _navigate_settings(page, _GMAIL_MARKERS_SETTINGS_URL)
            return
        marker_state = _set_importance_markers(page)
        if marker_state in {"clicked", "already"}:
            observer._markers_verified = True
            observer._emit("labels_markers.markers_enabled", "verified Importance markers: Show markers")
            _gmail_click_save_changes(page)
        return
    if not getattr(observer, "_labels_verified", False):
        if "settings/labels" not in fragment:
            _navigate_settings(page, _GMAIL_LABELS_SETTINGS_URL)
            return
        label_state = _show_spam_system_label(page)
        if label_state in {"clicked", "already"}:
            observer._labels_verified = True
            observer._emit("labels_markers.spam_shown", "verified Spam is visible in System labels")
            _gmail_click_save_changes(page)
        return
    if getattr(observer, "_labels_markers_completion_ticks", 0) < 2:
        observer._labels_markers_completion_ticks = getattr(observer, "_labels_markers_completion_ticks", 0) + 1
        return
    observer._emit("labels_markers.completed", "Gmail Importance markers enabled and Spam shown")
    observer._stop.set()


_GMAIL_INBOX_URL = "https://mail.google.com/mail/u/0/#inbox"
_GOOGLE_WORKSPACE_MARKETPLACE_URL = "https://workspace.google.com/marketplace/"
_GMAIL_ADDONS_SIDEBAR_SELECTOR = r"body > div.tVu25 > div.nH > div > div.nH.aqk.aql.bkL > div:nth-child(3) > div > div.bAw\.bcf.it > div.brC-dA-I-Jw > div"
_GMAIL_ADDONS_GET_SELECTOR = "#qJTzr > div:nth-child(3)"
_ADDONS_GET_LABELS = (
    "get add-ons",
    "get addons",
    "obtenir des modules complémentaires",
    "obtenir des modules complementaires",
    "obtener complementos",
    "obtener complementos de google workspace",
)
_ADDONS_RAIL_LABELS = (
    "add-ons",
    "addons",
    "modules complémentaires",
    "modules complementaires",
    "complementos",
)
_ADDONS_SEARCH_LABELS = (
    "search",
    "search apps",
    "search marketplace",
    "rechercher",
    "buscar",
)
_ADDONS_INSTALL_LABELS = ("install", "installer", "instalar", "obtenir", "get")
_ADDONS_ALREADY_INSTALLED_LABELS = (
    "uninstall",
    "désinstaller",
    "desinstaller",
    "desinstalar",
    "installed",
    "installé",
    "instalado",
)
_ADDONS_PERMISSION_TERMS = (
    "allow this application",
    "allow access",
    "autoriser",
    "autorizar",
    "permissions",
    "permissions requested",
    "access your google account",
    "acceder a tu cuenta de google",
    "accéder à votre compte google",
    "grant access",
    "paid plan",
    "free trial",
    "payment",
    "subscription",
)
_ADDONS_MARKETPLACE_CONTEXT_TERMS = (
    "install",
    "installer",
    "instalar",
    "marketplace",
    "add-on",
    "add-ons",
    "addon",
    "application",
    "app",
)
_ADDONS_EXTERNAL_CONSENT_TERMS = (
    "access your google account",
    "accéder à votre compte google",
    "acceder a tu cuenta de google",
    "third-party",
    "third party",
    "publisher",
    "see, edit, create, and delete",
    "voir, modifier, créer et supprimer",
    "ver, editar, crear y eliminar",
    "paid plan",
    "free trial",
    "payment",
    "subscription",
)
_ADDONS_AUTHORIZATION_LABELS = (
    "authorize",
    "autorise",
    "autoriser",
    "autorizar",
)
_ADDONS_MARKETPLACE_CONFIRM_LABELS = _ADDONS_AUTHORIZATION_LABELS + (
    "continue",
    "continuer",
    "continuar",
)


def _handle_addons_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    """Install up to three approved Marketplace add-ons in an authenticated profile.

    Gmail's add-on rail and Marketplace are both dynamic and localized. The
    flow therefore uses the supplied rail selector only as a first-choice
    toggle, then semantic visible labels for the rest. It accepts only the
    Marketplace installation confirmation; third-party OAuth, billing, and
    subscription prompts remain an operator hand-off.
    """
    observer._addons_tick_count += 1
    if observer._addons_tick_count > 240:
        observer._emit("addons.failed", "The Marketplace did not verify three add-on installations before the bounded timeout", result="failure")
        observer._stop.set()
        return
    if len(observer.addon_candidates) < 1:
        observer._emit("addons.failed", "No approved Gmail add-on candidates were available", result="failure")
        observer._stop.set()
        return

    candidate = observer.addon_candidates[observer._addons_current_index]
    name = candidate.get("name", "approved add-on")

    if host in _LOGIN_HOSTS:
        # Marketplace opens the Google authorization screen in a child popup
        # (accounts.google.com/signin/oauth). Handle only that exact OAuth
        # context and only when the page visibly identifies an add-on. A
        # normal login, MFA, or unrelated Google security page is left for the
        # normal login-policy hand-off.
        authorization = _addons_authorize_google_oauth(page, name)
        if authorization == "clicked":
            observer._addons_permission_reported = False
            observer._addons_wait_ticks = 0
            observer._emit("addons.oauth_authorized", f"approved Google's account authorization for {name}")
        elif authorization == "pause":
            observer._addons_wait_ticks += 1
            if not observer._addons_permission_reported:
                observer._addons_permission_reported = True
                observer._emit("addons.permission_required", f"A non-install permission or paid account approval is required before completing {name}", result="attention")
        return

    if host == "mail.google.com":
        if not observer._addons_sidebar_opened:
            if _click_gmail_addons_sidebar_toggle(page):
                observer._addons_sidebar_opened = True
                observer._addons_plus_scan_ticks = 0
                observer._addons_plus_scan_reported = False
                observer._emit("addons.sidebar_opened", "opened the Gmail add-ons sidebar")
            return
        if not observer._addons_get_opened:
            if not observer._addons_plus_clicked:
                observer._addons_plus_click_detail = None
                if _click_gmail_get_addons(page, report=lambda detail: setattr(observer, "_addons_plus_click_detail", detail)):
                    observer._addons_plus_clicked = True
                    observer._addons_plus_wait_ticks = 0
                    observer._addons_plus_scan_ticks = 0
                    observer._addons_plus_scan_reported = False
                    evidence = observer._addons_plus_click_detail or {}
                    selector = evidence.get("selector", "semantic visible control")
                    label = evidence.get("label", "")
                    frame_url = evidence.get("frame_url", "")
                    self_detail = f"clicked Gmail add-ons control selector={selector!r} label={label!r} frame={frame_url or 'mailbox'}"
                    observer._emit("addons.plus_clicked", self_detail)
                else:
                    observer._addons_plus_scan_ticks += 1
                    if observer._addons_plus_scan_ticks >= 3 and not observer._addons_plus_scan_reported:
                        observer._addons_plus_scan_reported = True
                        observer._emit("addons.plus_scan_waiting", "Gmail add-ons sidebar is open, but no visible + / Get add-ons control matched; continuing to rescan Gmail frames", result="attention")
                return
            # The plus button can open Marketplace as a child frame without
            # changing the outer Gmail URL. Give that frame time to appear;
            # if it does not, retry the scan instead of reporting success.
            observer._addons_plus_wait_ticks += 1
            if observer._addons_plus_wait_ticks >= 8:
                observer._addons_plus_clicked = False
                observer._addons_plus_wait_ticks = 0
                observer._addons_plus_scan_ticks = 0
                observer._addons_plus_scan_reported = False
            return
        return

    if host != "workspace.google.com":
        return

    observer._addons_get_opened = True
    observer._addons_plus_clicked = False
    observer._addons_plus_wait_ticks = 0
    if not observer._addons_marketplace_reported:
        observer._addons_marketplace_reported = True
        observer._emit("addons.marketplace_opened", "Marketplace surface is now visible after the Gmail + button click")

    if observer._addons_current_index >= len(observer.addon_candidates):
        observer._emit("addons.failed", f"Only {len(observer._addons_installed)} approved add-ons were installed; three were required", result="failure")
        observer._stop.set()
        return

    url = page.url or ""
    is_listing = "/marketplace/app/" in urlparse(url).path.lower()

    if not is_listing:
        observer._addons_wait_ticks += 1
        if not observer._addons_search_attempted:
            observer._addons_search_attempted = True
            if _addons_search_marketplace(page, name):
                observer._emit("addons.search_submitted", f"searched Marketplace for {name}")
                return
            # The Marketplace occasionally opens in a reduced shell without a
            # search field. The candidate URL is still an allowlisted official
            # Marketplace listing, so use it as a bounded fallback.
            _navigate_settings(page, url if "/marketplace/app/" in url else candidate["url"])
            observer._emit("addons.listing_opened", f"opened the allowlisted Marketplace listing for {name}")
            return
        if _addons_click_search_result(page, name):
            observer._emit("addons.search_result_opened", f"opened the Marketplace search result for {name}")
            return
        if observer._addons_wait_ticks >= 6:
            # The fallback starts a new page transition. Do not carry the
            # search wait counter into the listing or fail on the very first
            # listing tick while the Install control is still rendering.
            observer._addons_wait_ticks = 0
            _navigate_settings(page, candidate["url"])
            observer._emit("addons.listing_fallback", f"opened the allowlisted Marketplace listing for {name} after search results did not expose the card")
        return

    if not observer._addons_listing_opened:
        observer._addons_listing_opened = True
        observer._emit("addons.listing_ready", f"Marketplace listing ready for {name}")

    if _addons_listing_is_installed(page):
        observer._addons_skipped.append(name)
        observer._emit("addons.already_installed", f"{name} is already installed; counted toward the per-account maximum only once")
        _addons_advance(observer, page)
        return

    if not observer._addons_install_attempted:
        if _addons_click_install(page):
            observer._addons_install_attempted = True
            observer._addons_wait_ticks = 0
            observer._emit("addons.install_clicked", f"clicked Install for {name}")
        else:
            observer._addons_wait_ticks += 1
            if observer._addons_wait_ticks >= 8:
                observer._emit("addons.failed", f"Marketplace Install was not available for {name}", result="failure")
                observer._stop.set()
        return

    observer._addons_wait_ticks += 1
    authorization = _addons_authorize_marketplace_install(page, name)
    if authorization == "clicked":
        observer._addons_permission_reported = False
        observer._addons_wait_ticks = 0
        observer._emit("addons.install_authorized", f"approved the Google Marketplace installation confirmation for {name}")
        return
    if authorization == "pause" or _addons_permission_dialog_visible(page):
        if not observer._addons_permission_reported:
            observer._addons_permission_reported = True
            observer._emit("addons.permission_required", f"A non-Marketplace permission or account approval is required before completing {name}", result="attention")
        return
    observer._addons_permission_reported = False
    if _addons_listing_is_installed(page):
        observer._addons_installed.append(name)
        observer._emit("addons.installed", f"verified {name} is installed")
        if len(observer._addons_installed) >= 3:
            installed = ", ".join(observer._addons_installed)
            observer._emit("addons.completed", f"verified three Gmail add-ons: {installed}")
            observer._stop.set()
            return
        _addons_advance(observer, page)
        return
    if observer._addons_wait_ticks >= 20:
        observer._emit("addons.failed", f"Marketplace did not verify installation of {name}", result="failure")
        observer._stop.set()


def _click_gmail_addons_sidebar_toggle(page: "Page") -> bool:
    """Click Gmail's right-rail add-ons button, with a localized fallback.

    In some Gmail builds the supplied selector resolves to the rail container,
    while in others the actionable control is a tooltip-only ``+`` button.
    The fallback deliberately requires add-on wording so the generic side
    panel controls cannot be clicked accidentally.
    """
    try:
        clicked = page.evaluate(
            """selector => {
                const root = document.querySelector(selector);
                if (!root) return false;
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const visible = node => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const textOf = node => normalize([
                    node?.innerText,
                    node?.textContent,
                    node?.getAttribute?.('aria-label'),
                    node?.getAttribute?.('data-tooltip'),
                    node?.getAttribute?.('title'),
                ].filter(Boolean).join(' '));
                const addOnWords = [
                    'get add-ons',
                    'get addons',
                    'obtenir des modules complémentaires',
                    'obtenir des modules complementaires',
                    'obtener complementos',
                    'add-ons',
                    'addons',
                    'modules complémentaires',
                    'modules complementaires',
                    'complementos',
                ];
                const candidates = [root, ...root.querySelectorAll('button, [role="button"], [aria-label], [data-tooltip], [title]')]
                    .filter(visible);
                const target = candidates.find(node => addOnWords.some(word => textOf(node).includes(word)))
                    || (root.matches('button, [role="button"]') ? root : null)
                    || root.closest('button, [role="button"]');
                if (!target || !visible(target)) return false;
                target.click();
                return true;
            }""",
            _GMAIL_ADDONS_SIDEBAR_SELECTOR,
        )
        if clicked:
            return True
    except Exception:  # noqa: BLE001 - semantic fallback below
        pass
    if _click_element_with_text(page, _ADDONS_GET_LABELS):
        return True
    try:
        clicked = page.evaluate(
            """config => {
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const labels = [...config.get, ...config.rail].map(normalize);
                const visible = node => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const textOf = node => normalize([
                    node?.innerText,
                    node?.textContent,
                    node?.getAttribute?.('aria-label'),
                    node?.getAttribute?.('data-tooltip'),
                    node?.getAttribute?.('title'),
                ].filter(Boolean).join(' '));
                const candidates = [...document.querySelectorAll('button, [role="button"], [aria-label], [data-tooltip], [title]')]
                    .filter(visible)
                    .map(node => ({ node, text: textOf(node) }))
                    .filter(item => labels.some(label => item.text.includes(label)));
                const target = candidates.find(item => labels.slice(0, config.get.length).some(label => item.text.includes(label))) || candidates[0];
                if (!target) return false;
                target.node.click();
                return true;
            }""",
            {"get": _ADDONS_GET_LABELS, "rail": _ADDONS_RAIL_LABELS},
        )
        return bool(clicked)
    except Exception:  # noqa: BLE001 - the observer retries after Gmail re-renders
        return False


def _addons_authorize_marketplace_install(page: "Page", name: str) -> str | None:
    """Approve a clearly identified Marketplace install confirmation.

    Return ``clicked`` for the Google Marketplace confirmation, ``pause`` for
    an unrelated consent/security screen, and ``None`` when no modal exists.
    This keeps the automation from accepting a publisher's OAuth scopes or a
    paid/subscription prompt merely because it contains an Allow button.
    """
    for surface in _addons_surfaces(page):
        try:
            result = surface.evaluate(
                """config => {
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const contextTerms = config.context.map(normalize);
                const externalTerms = config.external.map(normalize);
                const labels = config.labels.map(normalize);
                const visible = node => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const textOf = node => normalize([
                    node?.innerText,
                    node?.textContent,
                    node?.getAttribute?.('aria-label'),
                    node?.getAttribute?.('title'),
                ].filter(Boolean).join(' '));
                const modalNodes = [...document.querySelectorAll('[role="dialog"], [role="alertdialog"], [aria-modal="true"]')]
                    .filter(visible)
                    .sort((a, b) => textOf(b).length - textOf(a).length);
                const roots = modalNodes.length ? modalNodes : (visible(document.body) ? [document.body] : []);
                for (const modal of roots) {
                    const modalText = textOf(modal);
                    const hasInstallContext = contextTerms.some(term => modalText.includes(term)) || modalText.includes(normalize(config.name));
                    const hasExternalConsent = externalTerms.some(term => modalText.includes(term));
                    const controls = [...modal.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"], a[aria-label]')].filter(visible);
                    if (hasInstallContext && !hasExternalConsent) {
                        const confirmation = controls.find(control => {
                            const label = textOf(control);
                            return labels.some(value => label === value || label.startsWith(value + ' '));
                        });
                        if (confirmation) {
                            confirmation.click();
                            return 'clicked';
                        }
                    }
                    // A Marketplace confirmation can include a short access
                    // summary. Only the explicit localized Authorize action
                    // may cross that boundary; generic Allow/Continue labels
                    // remain paused when the dialog looks like OAuth consent.
                    if (hasInstallContext && hasExternalConsent) {
                        const explicitAuthorization = controls.find(control => {
                            const label = textOf(control);
                            return labels.some(value => label === value || label.startsWith(value + ' '));
                        });
                        if (explicitAuthorization) {
                            explicitAuthorization.click();
                            return 'clicked';
                        }
                    }
                    const permissionText = ['permission', 'autorisation', 'permiso', 'allow access', 'autoriser', 'autorizar'].some(term => modalText.includes(term));
                    if (permissionText || hasExternalConsent) return 'pause';
                }
                return null;
            }""",
                {"context": _ADDONS_MARKETPLACE_CONTEXT_TERMS, "external": _ADDONS_EXTERNAL_CONSENT_TERMS, "labels": _ADDONS_MARKETPLACE_CONFIRM_LABELS, "name": name},
            )
            if result in {"clicked", "pause"}:
                return result
        except Exception:  # noqa: BLE001 - the next observer tick retries after a re-render
            continue
    return None


def _addons_authorize_google_oauth(page: "Page", name: str) -> str | None:
    """Approve the Google OAuth step spawned by a Marketplace install.

    Marketplace commonly opens a small ``accounts.google.com/signin/oauth``
    window and localizes the final action as Continue, Continuer, or
    Continuar. This is intentionally narrower than a generic Google consent
    click: the URL must be the OAuth route, the visible page must identify an
    add-on/application, and the copy must describe access to the Google
    account. Billing/subscription prompts are never accepted automatically.
    """
    try:
        parsed = urlparse(getattr(page, "url", "") or "")
        host = (parsed.hostname or "").lower().rstrip(".")
        if host != "accounts.google.com" or "/signin/oauth" not in parsed.path.lower():
            return None
    except Exception:  # noqa: BLE001 - a popup can close between ticks
        return None

    for surface in _addons_surfaces(page):
        try:
            result = surface.evaluate(
                """config => {
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const textOf = node => normalize([
                    node?.innerText,
                    node?.textContent,
                    node?.getAttribute?.('aria-label'),
                    node?.getAttribute?.('title'),
                ].filter(Boolean).join(' '));
                const visible = node => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const bodyText = textOf(document.body);
                const hasAddonContext = config.addonTerms.some(term => bodyText.includes(normalize(term)))
                    || (config.name && bodyText.includes(normalize(config.name)));
                const hasAccountConsent = config.consentTerms.some(term => bodyText.includes(normalize(term)));
                const blocked = config.blockedTerms.some(term => bodyText.includes(normalize(term)));
                if (!hasAddonContext || !hasAccountConsent) return null;
                if (blocked) return 'pause';
                const controls = [...document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]')].filter(visible);
                const action = controls.find(control => {
                    const label = textOf(control);
                    return config.labels.some(value => label === normalize(value) || label.startsWith(normalize(value) + ' '));
                });
                if (!action) return 'pause';
                action.click();
                return 'clicked';
            }""",
                {
                    "name": name,
                    "addonTerms": ["add-on", "add ons", "addon", "complemento", "complementos", "module complémentaire", "modules complémentaires", "application"],
                    "consentTerms": ["access your google account", "will allow", "allow this application", "acceda a la siguiente información", "accede a tu cuenta de google", "acceder a tu cuenta de google", "accéder à votre compte google", "autorisation d'accès", "google permitirá"],
                    "blockedTerms": ["payment", "subscription", "paid plan", "paiement", "abonnement", "suscripción", "plan de pago"],
                    "labels": ["continue", "continuer", "continuar", "next", "suivant", "siguiente"],
                },
            )
            if result in {"clicked", "pause"}:
                return result
        except Exception:  # noqa: BLE001 - popup content can re-render during approval
            continue
    return None


def _click_gmail_get_addons(page: "Page", report=None) -> bool:
    """Open Marketplace from the revealed rail in any Gmail child frame."""
    surfaces = [page]
    try:
        frames = getattr(page, "frames", [])
        if callable(frames):
            frames = frames()
        surfaces.extend(frame for frame in list(frames or []) if frame is not page)
    except Exception:  # noqa: BLE001 - a frame can detach during Gmail navigation
        pass

    # The user-provided selector is the most reliable signal for this build.
    # It is checked in every frame because Gmail may render the new rail in an
    # embedded panel rather than in the top-level document.
    for surface in surfaces:
        try:
            # Use Playwright's real pointer click for the exact user-supplied
            # selector when possible. A DOM ``element.click()`` can be ignored
            # by Gmail's event delegation even though it returns successfully.
            exact = surface.locator(_GMAIL_ADDONS_GET_SELECTOR)
            if exact.count() and exact.is_visible():
                exact.click(timeout=3000)
                if report:
                    report({"selector": _GMAIL_ADDONS_GET_SELECTOR, "label": "Get add-ons", "frame_url": getattr(surface, "url", "")})
                return True
        except Exception:  # noqa: BLE001 - fall through to DOM/frame scan
            pass
        try:
            clicked = surface.evaluate(
                """selector => {
                    const node = document.querySelector(selector);
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    if (style.visibility === 'hidden' || style.display === 'none' || rect.width <= 0 || rect.height <= 0) return false;
                    const target = node.closest('button, [role="button"]') || node;
                    target.click();
                    const rect = target.getBoundingClientRect();
                    return {clicked: true, selector, label: (target.innerText || target.getAttribute('aria-label') || target.getAttribute('data-tooltip') || '').trim(), frame_url: location.href, rect: {left: rect.left, top: rect.top, width: rect.width, height: rect.height}};
                }""",
                _GMAIL_ADDONS_GET_SELECTOR,
            )
            if clicked:
                if report:
                    report(clicked if isinstance(clicked, dict) else {"selector": _GMAIL_ADDONS_GET_SELECTOR, "frame_url": getattr(surface, "url", "")})
                return True
        except Exception:  # noqa: BLE001 - try the next frame or fallback
            continue

    # Some Gmail releases render the plus as an unlabeled icon in a narrow
    # right rail. Use viewport geometry only after the explicit selector: the
    # target must be on the right edge, be visible, and contain either a real
    # plus glyph or add-on wording in its accessible metadata.
    for surface in surfaces:
        try:
            clicked = surface.evaluate(
                """config => {
                    const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const addOnWords = config.labels.map(normalize);
                    const visible = node => {
                        if (!node) return false;
                        const style = window.getComputedStyle(node);
                        const rect = node.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                    };
                    const textOf = node => normalize([
                        node?.innerText,
                        node?.textContent,
                        node?.getAttribute?.('aria-label'),
                        node?.getAttribute?.('data-tooltip'),
                        node?.getAttribute?.('title'),
                    ].filter(Boolean).join(' '));
                    const width = window.innerWidth || document.documentElement.clientWidth || 0;
                    const height = window.innerHeight || document.documentElement.clientHeight || 0;
                    const isRightRail = rect => rect.left >= Math.max(0, width - Math.max(180, width * 0.28)) && rect.top > 40 && rect.top < height - 20 && rect.width <= Math.max(180, width * 0.32);
                    const controls = [...document.querySelectorAll('button, [role="button"], [aria-label], [data-tooltip], [title], div, span')]
                        .filter(visible)
                        .map(node => ({ node, rect: node.getBoundingClientRect(), text: textOf(node) }))
                        .filter(item => isRightRail(item.rect));
                    const plus = controls.filter(item => /(^|\\s)\\+(?=\\s|$)/.test(item.text) || item.text === '+' || addOnWords.some(label => item.text.includes(label)));
                    if (!plus.length) return false;
                    // Prefer an explicit add-on label. Otherwise choose the
                    // lowest visible plus control, which is the Marketplace
                    // button at the bottom of Gmail's right rail.
                    plus.sort((a, b) => {
                        const aLabel = addOnWords.some(label => a.text.includes(label)) ? 0 : 1;
                        const bLabel = addOnWords.some(label => b.text.includes(label)) ? 0 : 1;
                        return aLabel - bLabel || b.rect.top - a.rect.top;
                    });
                    const node = plus[0].node;
                    const target = node.closest('button, [role="button"]') || node;
                    target.click();
                    const rect = target.getBoundingClientRect();
                    return {clicked: true, selector: 'geometry-right-rail', label: (target.innerText || target.getAttribute('aria-label') || target.getAttribute('data-tooltip') || '').trim(), frame_url: location.href, rect: {left: rect.left, top: rect.top, width: rect.width, height: rect.height}};
                }""",
                {"labels": _ADDONS_GET_LABELS + _ADDONS_RAIL_LABELS},
            )
            if clicked:
                if report:
                    report(clicked if isinstance(clicked, dict) else {"selector": "geometry-right-rail", "frame_url": getattr(surface, "url", "")})
                return True
        except Exception:  # noqa: BLE001 - try the next frame or fallback
            continue

    for surface in surfaces:
        if _click_element_with_text(surface, _ADDONS_GET_LABELS):
            if report:
                report({"selector": "semantic-visible-label", "label": "get add-ons" , "frame_url": getattr(surface, "url", "")})
            return True

    for surface in surfaces:
        try:
            clicked = surface.evaluate(
                """labels => {
                    const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const wanted = labels.map(normalize);
                    const visible = node => {
                        if (!node) return false;
                        const style = window.getComputedStyle(node);
                        const rect = node.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                    };
                    const textOf = node => normalize([
                        node?.innerText,
                        node?.textContent,
                        node?.getAttribute?.('aria-label'),
                        node?.getAttribute?.('data-tooltip'),
                        node?.getAttribute?.('title'),
                    ].filter(Boolean).join(' '));
                    const control = [...document.querySelectorAll('button, [role="button"], [aria-label], [data-tooltip], [title]')]
                        .find(node => visible(node) && wanted.some(label => textOf(node).includes(label)));
                    if (!control) return false;
                    control.click();
                    const rect = control.getBoundingClientRect();
                    return {clicked: true, selector: 'localized-visible-control', label: (control.innerText || control.getAttribute('aria-label') || control.getAttribute('data-tooltip') || '').trim(), frame_url: location.href, rect: {left: rect.left, top: rect.top, width: rect.width, height: rect.height}};
                }""",
                _ADDONS_GET_LABELS,
            )
            if clicked:
                if report:
                    report(clicked if isinstance(clicked, dict) else {"selector": "localized-visible-control", "frame_url": getattr(surface, "url", "")})
                return True
        except Exception:  # noqa: BLE001 - the observer retries after Gmail re-renders
            continue
    return False


def _addons_search_marketplace(page: "Page", name: str) -> bool:
    try:
        for field in page.locator("input").all():
            try:
                if not field.is_visible():
                    continue
                label = " ".join((field.get_attribute("aria-label") or "", field.get_attribute("placeholder") or "")).strip().lower()
                if not any(term in label for term in _ADDONS_SEARCH_LABELS):
                    continue
                field.fill(name, timeout=2000)
                field.press("Enter", timeout=2000)
                return True
            except Exception:  # noqa: BLE001 - try the next visible search field
                continue
    except Exception:  # noqa: BLE001
        return False
    return False


def _addons_surfaces(page: object) -> list[object]:
    """Return a surface and its live child frames without failing on detach."""
    surfaces = [page]
    try:
        frames = getattr(page, "frames", [])
        if callable(frames):
            frames = frames()
        surfaces.extend(frame for frame in list(frames or []) if frame is not page)
    except Exception:  # noqa: BLE001 - a frame may detach during a popup transition
        pass
    return surfaces


def _addons_control_text(control: object) -> str:
    values: list[str] = []
    try:
        values.append(control.inner_text() or "")
    except Exception:  # noqa: BLE001 - some controls expose only aria text
        pass
    for attribute in ("aria-label", "data-tooltip", "title", "value"):
        try:
            value = control.get_attribute(attribute)
        except Exception:  # noqa: BLE001 - test doubles and detached nodes
            value = None
        if value:
            values.append(value)
    return " ".join(values).replace("\u00a0", " ").strip().lower()


def _addons_click_search_result(page: "Page", name: str) -> bool:
    wanted = name.lower().replace("™", "").strip()
    try:
        for card in page.locator('a[href*="/marketplace/app/"], [role="link"]').all():
            try:
                if not card.is_visible():
                    continue
                href = card.get_attribute("href") or ""
                text = (card.inner_text() or "").replace("™", "").strip().lower()
                if "/marketplace/app/" not in href and wanted not in text:
                    continue
                if wanted not in text:
                    continue
                card.click(timeout=2000)
                return True
            except Exception:  # noqa: BLE001 - keep scanning result cards
                continue
    except Exception:  # noqa: BLE001
        return False
    return False


def _addons_click_install(page: "Page") -> bool:
    """Click the first visible Marketplace install action in the listing.

    Marketplace cards do not use one stable DOM shape. The action text may be
    inside a button, a role button, an anchor, or an aria-only control. Read
    all of those signals and explicitly exclude uninstall/already-installed
    controls so the first card's install action is the one selected.
    """
    for surface in _addons_surfaces(page):
        try:
            for control in surface.locator("button, [role=button], a, input[type=button], input[type=submit]").all():
                try:
                    if not control.is_visible():
                        continue
                    text = _addons_control_text(control)
                    if any(term in text for term in _ADDONS_ALREADY_INSTALLED_LABELS):
                        continue
                    if any(text == term or text.startswith(term + " ") or f" {term} " in text for term in _ADDONS_INSTALL_LABELS):
                        control.click(timeout=2000)
                        return True
                except Exception:  # noqa: BLE001 - keep scanning localized controls
                    continue
        except Exception:  # noqa: BLE001 - continue through child frames
            continue
    return False


def _addons_listing_is_installed(page: "Page") -> bool:
    for surface in _addons_surfaces(page):
        try:
            for control in surface.locator("button, [role=button], a, input[type=button], input[type=submit]").all():
                if not control.is_visible():
                    continue
                text = _addons_control_text(control)
                if text in _ADDONS_ALREADY_INSTALLED_LABELS:
                    return True
        except Exception:  # noqa: BLE001 - continue through child frames
            continue
    return False


def _addons_permission_dialog_visible(page: "Page") -> bool:
    for surface in _addons_surfaces(page):
        try:
            for dialog in surface.locator('[role="dialog"], [aria-modal="true"]').all():
                if not dialog.is_visible():
                    continue
                text = _addons_control_text(dialog).replace("\n", " ")
                if any(term in text for term in _ADDONS_PERMISSION_TERMS):
                    return True
        except Exception:  # noqa: BLE001 - continue through child frames
            continue
    return False


def _addons_advance(observer: "BrowserObserver", page: "Page") -> None:
    observer._addons_current_index += 1
    observer._addons_search_attempted = False
    observer._addons_listing_opened = False
    observer._addons_install_attempted = False
    observer._addons_wait_ticks = 0
    _navigate_settings(page, _GOOGLE_WORKSPACE_MARKETPLACE_URL)


def _handle_security_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    """Enable and verify Enhanced Safe Browsing in the signed-in profile.

    The My Account surface is a client-rendered page whose DOM changes across
    locales and releases. The helper below finds a semantic switch/checkbox
    in the container labelled for Enhanced Safe Browsing, rather than relying
    on a generated CSS path or a screen coordinate.
    """
    observer._security_tick_count = getattr(observer, "_security_tick_count", 0) + 1
    if observer._security_tick_count > 120:
        observer._emit("security.failed", "Enhanced Safe Browsing did not reach a verified enabled state", result="failure")
        observer._stop.set()
        return
    if host != "myaccount.google.com":
        return
    url = page.url or ""
    if _SAFE_BROWSING_PAGE_PATH not in urlparse(url).path.lower():
        _navigate_settings(page, _GOOGLE_ENHANCED_SAFE_BROWSING_URL)
        return
    confirmation = _confirm_enhanced_safe_browsing(page)
    if confirmation:
        observer._emit("security.activation_confirmed", f"confirmed Enhanced Safe Browsing with {confirmation}")
        return
    state = _set_enhanced_safe_browsing(page)
    if state == "clicked":
        observer._security_clicked = True
        observer._emit("security.toggle_clicked", "selected Enhanced Safe Browsing")
        return
    if state == "already":
        observer._security_verified = True
    if not observer._security_verified:
        return
    if getattr(observer, "_security_completion_ticks", 0) < 2:
        observer._security_completion_ticks = getattr(observer, "_security_completion_ticks", 0) + 1
        return
    observer._emit("security.completed", "Enhanced Safe Browsing enabled and verified")
    observer._stop.set()


def _confirm_enhanced_safe_browsing(page: "Page") -> str | None:
    """Click the confirmation dialog's localized activation action.

    Clicking the first toggle can open a second Google Account dialog. Its
    action is commonly ``Activate`` / ``Activer`` / ``Activar``. Search only
    visible controls inside a modal and require Enhanced Safe Browsing context
    when the dialog exposes it; this avoids pressing similarly named controls
    elsewhere on the account page.
    """
    try:
        result = page.evaluate(
            """config => {
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const terms = config.terms.map(normalize);
                const labels = config.labels.map(normalize);
                const visible = node => {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                };
                const textOf = node => normalize([
                    node?.innerText,
                    node?.textContent,
                    node?.getAttribute?.('aria-label'),
                    node?.getAttribute?.('title'),
                ].filter(Boolean).join(' '));
                const modals = [...document.querySelectorAll('[role="dialog"], [role="alertdialog"], [aria-modal="true"]')]
                    .filter(visible)
                    .sort((a, b) => textOf(b).length - textOf(a).length);
                for (const modal of modals) {
                    const modalText = textOf(modal);
                    const hasSafeBrowsingContext = terms.some(term => modalText.includes(term));
                    const controls = [...modal.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"]')]
                        .filter(visible);
                    for (const control of controls) {
                        const label = textOf(control);
                        const matches = labels.some(value => label === value || label.startsWith(value + ' '));
                        if (matches && (hasSafeBrowsingContext || labels.includes(label))) {
                            control.click();
                            return label;
                        }
                    }
                }
                return null;
            }""",
            {"terms": _SAFE_BROWSING_TERMS, "labels": _SAFE_BROWSING_ACTIVATE_LABELS},
        )
        if not isinstance(result, str):
            return None
        normalized = result.strip().lower()
        if normalized in _SAFE_BROWSING_ACTIVATE_LABELS or any(normalized.startswith(f"{label} ") for label in _SAFE_BROWSING_ACTIVATE_LABELS):
            return normalized
        return None
    except Exception:  # noqa: BLE001 - the next observer tick retries after a re-render
        return None


def _set_enhanced_safe_browsing(page: "Page") -> str | None:
    """Return ``clicked`` or ``already`` for the localized security control."""
    try:
        result = page.evaluate(
            """config => {
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const terms = config.terms.map(normalize);
                const action = ['turn on', 'enable', 'activate', 'keep on', 'keep it on', 'activer', 'garder active', 'maintenir active', 'activa', 'activar', 'mantener activado', 'habilitar'];
                const controls = [...document.querySelectorAll('input[type="checkbox"], [role="switch"], [role="checkbox"], button')];
                const textOf = node => normalize([node?.innerText, node?.textContent, node?.getAttribute?.('aria-label'), node?.getAttribute?.('title')].filter(Boolean).join(' '));
                const hasTerm = value => terms.some(term => value.includes(term));
                const checked = node => node.checked === true || node.getAttribute('aria-checked') === 'true' || node.getAttribute('aria-pressed') === 'true' || /\\b(on|enabled|active|selected|checked)\\b/i.test(node.className || '');
                for (const control of controls) {
                    let scope = control;
                    let scopeText = '';
                    for (let depth = 0; scope && depth < 5; depth += 1, scope = scope.parentElement) {
                        const candidate = textOf(scope);
                        if (candidate.length > scopeText.length && candidate.length < 1200) scopeText = candidate;
                        if (hasTerm(candidate)) break;
                    }
                    const ownText = textOf(control);
                    const label = control.labels?.[0] ? textOf(control.labels[0]) : '';
                    const context = [ownText, label, scopeText].join(' ');
                    if (!hasTerm(context)) continue;
                    if (checked(control)) return 'already';
                    const actionText = context;
                    if (control.matches('button') && !action.some(term => actionText.includes(term)) && !control.getAttribute('aria-checked')) continue;
                    control.click();
                    return 'clicked';
                }
                const body = normalize(document.body?.innerText);
                const enabledPattern = new RegExp('(?:' + terms.join('|') + ').{0,100}(?:on|enabled|active|turned on|activee|activado|activada|active)', 'i');
                if (enabledPattern.test(body)) return 'already';
                return null;
            }""",
            {"terms": _SAFE_BROWSING_TERMS},
        )
        return result if result in {"clicked", "already"} else None
    except Exception:  # noqa: BLE001 - the next observer tick retries after a re-render
        return None


def _resolve_credential_change_material(observer: "BrowserObserver") -> dict[str, object]:
    """Resolve the pending password/2FA material for a credential-change session."""
    if not observer._credential_material_tried:
        observer._credential_material_tried = True
        if observer._resolve_credential_change is not None:
            try:
                material = observer._resolve_credential_change(observer.session_id)
                if isinstance(material, dict):
                    observer._credential_material = material
            except Exception:  # noqa: BLE001
                logger.exception("credential-change material resolution failed for session %s", observer.session_id)
    return observer._credential_material or {}


def _observer_current_password(observer: "BrowserObserver") -> str | None:
    """Return the stored account password for the current-password step."""
    try:
        if observer._resolve_current_password is not None:
            resolved = observer._resolve_current_password(observer.session_id)
            if resolved:
                return str(resolved)
    except Exception:  # noqa: BLE001
        logger.exception("current-password resolution failed for session %s", observer.session_id)
    if observer._resolve_credentials is None:
        return None
    try:
        credentials = observer._resolve_credentials(observer.session_id)
        if not credentials or len(credentials) < 2:
            return None
        return str(credentials[1])
    except Exception:  # noqa: BLE001
        logger.exception("current-password resolution failed for session %s", observer.session_id)
        return None


def _write_credential_sheets(observer: "BrowserObserver", material: dict[str, object], include_two_fa: bool = True) -> None:
    """Persist the new password and the 2FA secret as plain text files inside the
    profile directory so the operator can find them on disk after the session."""
    from datetime import datetime, timezone

    profile_dir = Path(observer.profile_dir) if observer.profile_dir else None
    if not profile_dir:
        return
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        logger.warning("could not create profile dir for credential sheets", exc_info=True)
        return
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    password = material.get("password")
    if password and not observer._password_sheet_written:
        try:
            target = profile_dir / "new-password.txt"
            target.write_text(
                f"Google Workspace password set by automation\nsaved at {stamp}\naccount profile: {observer.profile_id}\n\n{password}\n",
                encoding="utf-8",
            )
            try:
                os.chmod(target, 0o600)
            except Exception:  # noqa: BLE001
                pass
            observer._password_sheet_written = True
            logger.info("session %s: wrote new-password.txt into the profile", observer.session_id)
        except Exception:  # noqa: BLE001
            logger.warning("could not write new-password.txt for session %s", observer.session_id, exc_info=True)
    secret = material.get("2fa")
    if secret and include_two_fa and not observer._two_step_sheet_written:
        try:
            target = profile_dir / "twofa-secret.txt"
            target.write_text(
                f"Google Workspace authenticator base32 secret generated by automation\nsaved at {stamp}\naccount profile: {observer.profile_id}\n\n{secret}\n",
                encoding="utf-8",
            )
            try:
                os.chmod(target, 0o600)
            except Exception:  # noqa: BLE001
                pass
            observer._two_step_sheet_written = True
            logger.info("session %s: wrote twofa-secret.txt into the profile", observer.session_id)
        except Exception:  # noqa: BLE001
            logger.warning("could not write twofa-secret.txt for session %s", observer.session_id, exc_info=True)


def _drive_password_change_form(page: "Page", current_password: str, new_password: str, stage: int = 0, submitted: bool = False) -> str:
    """Fill and submit Google's (staged) password-change form.

    Google renders the change flow in two steps: a single current-password
    field first, then the new + confirm fields. The form controls can also be
    layered inside open shadow roots, so this collects nodes recursively.

    Returns ``submitted1`` (current-password step), ``submitted2`` (new-password
    step), ``filled``, ``waiting``, ``done`` or ``error``.
    """
    try:
        result = page.evaluate(
            """config => {
                const current = config.current;
                const fresh = config.fresh;
                const stage = config.stage;
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const isVisible = node => {
                    if (!node || !node.isConnected) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
                };
                const setValue = (el, value) => {
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    setter.call(el, value);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                };
                const collect = (root, out) => {
                    for (const el of root.querySelectorAll('input[type="password"], button, [role="button"], input[type="submit"], input[type="button"]')) out.push(el);
                    for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) collect(el.shadowRoot, out); }
                };
                const nodes = [];
                collect(document, nodes);
                const inputs = [...new Set(nodes.filter(n => n.matches && n.matches('input[type="password"]') && isVisible(n)))];
                const buttons = [...new Set(nodes.filter(n => n.matches && (n.matches('button, [role="button"], input[type="submit"], input[type="button"]')) && isVisible(n)))];
                const ctrlLabel = input => normalize([input.getAttribute('aria-label'), input.getAttribute('placeholder')].filter(Boolean).join(' '));
                const labelOf = node => normalize([node.innerText, node.getAttribute('aria-label'), node.textContent].filter(Boolean).join(' '));
                const skipTerms = /(forgot|oubli|olvid|annul|canc|forgot your password)/;
                const matchesAny = (label, terms) => terms.some(term => label === term || label.startsWith(term + ' ') || label.endsWith(' ' + term) || label.includes(term));
                const bodyText = normalize(document.body && document.body.innerText || '');
                const successRe = new RegExp(config.successRe);
                if (/(something went wrong|couldn.t (change|update)|unable to (change|update)|wrong password|your password may not be correct|an error occurred|se produjo un error|no se pudo (cambiar|actualizar|modificar)|ocurrio un error|intenta (de )?nuevo)/.test(bodyText)) {
                    return 'error';
                }
                if ((config.stage >= 1 || config.submitted) && successRe.test(bodyText)) {
                    return 'done';
                }
                if (!inputs.length) {
                    if (successRe.test(bodyText)) return 'done';
                    return 'waiting';
                }
                const submitButton = buttons.find(button => {
                    const label = labelOf(button);
                    if (!label || skipTerms.test(label)) return false;
                    if (matchesAny(label, config.submitLabels)) return true;
                    return /(^|\\s)(change|update|save|modifier|cambiar|actualizar)\\b[^]{0,40}\\b(password|contrasena|mot\\s*de\\s*passe)\\b/.test(label)
                        || /(^|\\s)(password|contrasena|mot\\s*de\\s*passe)\\b[^]{0,40}\\b(change|update|save|modifier|cambiar|actualizar)\\b/.test(label);
                });
                const continueButton = buttons.find(button => {
                    const label = labelOf(button);
                    if (!label || skipTerms.test(label)) return false;
                    return matchesAny(label, config.continueTerms) || matchesAny(label, config.submitLabels);
                });
                // Single visible password field = the current-password step.
                if (inputs.length === 1) {
                    const field = inputs[0];
                    if (stage >= 1) {
                        if (!field.value) { setValue(field, current); return 'filled'; }
                        return 'waiting';
                    }
                    if (!field.value) { setValue(field, current); return 'filled'; }
                    if (!config.submitted && field.value === current) {
                        if (continueButton && !continueButton.disabled) {
                            continueButton.click();
                            return 'submitted1';
                        }
                        // No recognizable/clickable button: the observer presses
                        // the real Enter key on the focused field instead.
                        return 'keyboard';
                    }
                    return 'waiting';
                }
                // Two+ visible password fields = the new-password step.
                const newPattern = /(^|\\s)(new password|nueva contrasena|contrasena nueva|nueva clave|clave nueva|nuevo contrasena|nouveau mot de passe)\\b/;
                const confirmPattern = /(confirm new password|confirm password|re[- ]?enter|retype|repeat|repita|vuelve a|repite|confirmer|coincide|match)/;
                let newField = null;
                let confirmField = null;
                for (const input of inputs) {
                    const label = ctrlLabel(input);
                    if (newPattern.test(label)) { if (!newField) newField = input; }
                    else if (confirmPattern.test(label)) { if (!confirmField) confirmField = input; }
                }
                if (!newField) newField = inputs[0];
                if (!confirmField && inputs.length > 1) {
                    confirmField = inputs[inputs.length - 1] === newField ? inputs[0] : inputs[inputs.length - 1];
                }
                let filledAny = false;
                if (newField && !newField.value) { setValue(newField, fresh); filledAny = true; }
                if (confirmField && !confirmField.value) { setValue(confirmField, fresh); filledAny = true; }
                if (!config.submitted && newField && confirmField && newField.value && confirmField.value && submitButton && !submitButton.disabled) {
                    submitButton.click();
                    return 'submitted2';
                }
                if (!config.submitted && newField && confirmField && newField.value && confirmField.value) {
                    // No recognizable/clickable submit button: fall back to the
                    // real-keyboard Enter press handled by the observer. This is
                    // the final submit, so the caller must start confirming it.
                    return 'keyboard2';
                }
                return filledAny ? 'filled' : 'waiting';
            }""",
            {"current": current_password, "fresh": new_password, "stage": stage, "submitted": bool(submitted), "submitLabels": _PASSWORD_CHANGE_SUBMIT_LABELS, "continueTerms": ("continue", "next", "confirm", "enter", "submit", "siguiente", "continuar", "confirmar", "enviar", "acceder", "entrar"), "successRe": _PASSWORD_CHANGE_SUCCESS_JS_RE},
        )
        if result in {"submitted1", "submitted2", "keyboard", "keyboard2", "filled", "waiting", "done", "error"}:
            return result
        return "waiting"
    except Exception:  # noqa: BLE001 - the next observer tick retries after a re-render
        return "waiting"


def _drive_two_step_setup(page: "Page", current_password: str | None, removed: bool = False, have_key: bool = False, code_submitted: bool = False) -> dict[str, object]:
    """Walk Google's Two-Step Verification authenticator page.

    Handles ``https://myaccount.google.com/two-step-verification/authenticator``
    (and the legacy ``signinoptions`` path): removes any existing authenticator,
    restarts the "set up authenticator" flow, chooses the *secret key* manual
    entry option, captures the base32 pairing key Google generates and verifies
    the OTP generated from it.

    Returns ``{"state": "done"|"waiting"|"error", "key": str|null,
    "action": str|null}``; ``key`` is Google's pairing secret once the manual
    entry screen is visible.
    """
    try:
        result = page.evaluate(
            """config => {
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/[?¿!¡.,:;()\\[\\]{}"'\u00ab\u00bb\u2026]/g, ' ').replace(/\\s+/g, ' ').trim().toLowerCase();
                const isVisible = node => {
                    if (!node || !node.isConnected) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
                };
                const collect = (root, out) => {
                    for (const el of root.querySelectorAll('button, [role="button"], a, [role="link"], input, [role="radio"], [role="checkbox"]')) out.push(el);
                    for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) collect(el.shadowRoot, out); }
                };
                const nodes = [];
                collect(document, nodes);
                const unique = [...new Set(nodes)];
                const visible = unique.filter(isVisible);
                const labelOf = node => normalize([node.innerText, node.textContent, node.getAttribute('aria-label'), node.getAttribute('value'), node.getAttribute('placeholder')].filter(Boolean).join(' '));
                const isInteractive = node => node.matches && node.matches('button, [role="button"], a, [role="link"], input[type="button"], input[type="submit"]');
                const buttons = visible.filter(isInteractive);
                const wanted = list => list.map(normalize).filter(Boolean);
                const matchesAny = (label, list) => list.some(term => label === term || label.startsWith(term + ' ') || label.endsWith(' ' + term));
                const clickButton = (list, skip) => {
                    const targets = wanted(list);
                    for (const node of buttons) {
                        if (node.disabled) continue;
                        const label = labelOf(node);
                        if (!label) continue;
                        if (skip && skip.test(label)) continue;
                        if (matchesAny(label, targets)) { try { node.click(); return label; } catch (error) {} }
                    }
                    return null;
                };
                let textParts = [];
                const seenText = new Set();
                const gatherText = root => {
                    for (const el of root.querySelectorAll('*')) {
                        if (seenText.has(el)) continue;
                        seenText.add(el);
                        if (el.childElementCount > 0) continue;
                        if (!isVisible(el)) continue;
                        const value = (el.innerText || el.textContent || '').trim();
                        if (value) textParts.push({ node: el, value: value });
                    }
                    for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) gatherText(el.shadowRoot); }
                };
                gatherText(document);
                const text = normalize(textParts.map(part => part.value).join(' \\n '));
                const bodyText = normalize(document.body && document.body.innerText || '');
                const haystack = text + ' \\n ' + bodyText;
                const skip = /(forgot|oubli|olvid|cancel|annul|canc|back|retour|atrás|atras)/;
                const scan = { hasPassword: false, hasSetupKey: false, hasCodeInput: false, hasDialog: false, hasExistingRow: false, hasGetStarted: false, hasManual: false, clicked: null };
                // Never complete from a static "2-Step Verification is on" banner:
                // the plan must remove an existing authenticator and capture a NEW
                // key before it is confirmed, so "done" is only valid after the new
                // secret has been captured and verified.
                // After Google accepts the generated OTP, stop touching setup
                // controls. The settings page can take several ticks to render
                // its success banner/device row; re-clicking during that gap
                // removes the newly-added authenticator and starts over.
                if (config.haveKey && config.codeSubmitted) {
                    if (config.doneTerms.some(term => haystack.includes(term))) return { state: 'done', key: null, action: null, scan };
                    const setupDialog = [...document.querySelectorAll('[role="dialog"], dialog[open], [role="alertdialog"], [aria-modal="true"]')].some(isVisible);
                    const authenticatorVisible = !setupDialog && config.authNameTerms.some(term => haystack.includes(term));
                    if (authenticatorVisible) return { state: 'done', key: null, action: 'confirmed-device', scan };
                    return { state: 'waiting', key: null, action: 'verification-pending', scan };
                }

                const passwordFields = visible.filter(node => node.matches && node.matches('input[type="password"]'));
                scan.hasPassword = passwordFields.length > 0;
                if (passwordFields.length) {
                    const field = passwordFields[0];
                    if (config.current && !field.value) {
                        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                        setter.call(field, config.current);
                        field.dispatchEvent(new Event('input', { bubbles: true }));
                        field.dispatchEvent(new Event('change', { bubbles: true }));
                        return { state: 'waiting', key: null, action: 'password-filled', scan };
                    }
                    const clicked = config.current ? clickButton(config.nextLabels, skip) : null;
                    // Password is in (Google's re-auth prompt often draws its
                    // submit as an unlabelled icon button): press the real
                    // Enter key instead of leaving the box filled but unsubmitted.
                    if (!clicked && config.current) return { state: 'waiting', key: null, action: 'password-keyboard', scan };
                    return { state: 'waiting', key: null, action: clicked ? 'password-submitted' : 'password', scan };
                }

                // Only a base32-looking token that is trusted is captured as the
                // pairing secret. Trust sources: an editable/readonly input, a
                // key box (monospace or key/secret/setup marked), or a token with
                // real digit content (2-7) beyond a single leading digit. Without
                // this, page words and banners such as "GOOGLEAUTHENTICATOR" or
                // "2-STEP VERIFICATION IS ON" were captured as the key and the
                // flow never started.
                const plausibleKeyToken = value => /^[A-Z2-7]{16,32}$/.test(value);
                const inKeyBox = node => {
                    let el = node;
                    while (el && el !== document.body) {
                        try {
                            const f = window.getComputedStyle(el).fontFamily || '';
                            if (/mono/i.test(f)) return true;
                            const marker = (el.className || '') + ' ' + (el.id || '');
                            if (/(key|secret|setup)/i.test(marker)) return true;
                        } catch (error) {}
                        el = el.parentElement;
                    }
                    return false;
                };
                const trustToken = (value, node) => {
                    if (node && node.matches && node.matches('input')) return true;
                    if (inKeyBox(node)) return true;
                    const digits = (value.match(/[2-7]/g) || []).length;
                    if (digits < 1) return false;
                    if (digits >= 2) return true;
                    // A single digit is only trusted when it is not a leading
                    // headline digit ("2-STEP VERIFICATION IS ON" style).
                    return !/^[2-7][A-Z]*$/.test(value);
                };
                let key = null;
                for (const node of visible) {
                    if (!node.matches || !node.matches('input[readonly], input[type="text"], input[type="tel"], input:not([type])')) continue;
                    const raw = (node.value || '').trim();
                    const value = raw.replace(/[\\s-]/g, '').toUpperCase();
                    if (plausibleKeyToken(value) && trustToken(value, node)) { key = value; break; }
                }
                if (!key) {
                    for (const part of textParts) {
                        const compact = part.value.replace(/[\\s-]/g, '').toUpperCase();
                        if (plausibleKeyToken(compact) && trustToken(compact, part.node)) { key = compact; break; }
                    }
                }
                if (!key) {
                    // Some Google locales render the setup secret as several
                    // adjacent four-character text runs instead of one input
                    // or one text node. Recover only a strict grouped Base32
                    // token, never arbitrary page copy.
                    const groupedKey = value => {
                        const match = String(value || '').match(/\\b([A-Z2-7]{4}(?:[\\s-]+[A-Z2-7]{4}){3,7})\\b/i);
                        if (!match) return null;
                        const compact = match[1].replace(/[\\s-]/g, '').toUpperCase();
                        return plausibleKeyToken(compact) && trustToken(compact, null) ? compact : null;
                    };
                    key = groupedKey(text) || groupedKey(bodyText);
                }
                scan.hasSetupKey = !!key;
                if (key) {
                    // The manual-key screen in the screenshot is not the end
                    // of setup. Google shows the pairing key and then requires
                    // a localized Next/Siguiente/Suivant click before it
                    // renders the verification-code field. Without this click
                    // every observer tick sees the same key and loops forever.
                    const next = clickButton(config.nextLabels, skip);
                    scan.clicked = next ? 'next-after-key' : null;
                    return { state: 'waiting', key: key, action: next ? 'key-next' : 'key', scan };
                }

                const isCodeField = node => {
                    if (!node.matches || !node.matches('input')) return false;
                    const label = labelOf(node);
                    return node.matches('input[autocomplete="one-time-code"], input[type="tel"], input[inputmode="numeric"]')
                        || /(code|codigo|verification|verificacion|verifier|codice|verifizierung|digit|digito|chiffre)/i.test(label);
                };
                const codeInputs = visible.filter(isCodeField);
                scan.hasCodeInput = codeInputs.length > 0;
                if (codeInputs.length && config.haveKey) return { state: 'waiting', key: null, action: 'code', scan };
                // Before the setup starts Google may wall the security page behind a
                // "verify it's you" challenge asking for the CURRENT account's TOTP.
                // The shared login hooks only cover accounts/consent hosts, so this
                // drive surfaces the prompt on myaccount for the handler to fill with
                // the stored secret (the setup code is only filled after the new key
                // is captured, hence the haveKey/removed guards).
                if (codeInputs.length && !config.haveKey && !config.removed) return { state: 'waiting', key: null, action: 'reauth-code', scan };

                // Removal confirmation pop-up: Google asks "Remove authenticator?"
                // (Spanish: "¿Quitar el Autenticador?") in a Material dialog whose
                // confirm button is the second half of the delete. The dialog must
                // be matched and its confirm clicked *within the dialog only*: the
                // trash button carries a similar aria-label ("Quitar el Autenticador
                // de Google"), so a global search would re-click behind the modal
                // forever instead of confirming.
                const dialog = (() => {
                    // Dialogs are containers, not interactive nodes, so they are
                    // absent from `visible`; collect them explicitly (shadow DOM
                    // included) or the confirmation pop-up is never matched.
                    const found = [];
                    const collectDialogs = root => {
                        for (const el of root.querySelectorAll('[role="dialog"], dialog[open], [role="alertdialog"], [aria-modal="true"], .VfPpkd-Sx9Kwc')) found.push(el);
                        for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) collectDialogs(el.shadowRoot); }
                    };
                    collectDialogs(document);
                    return [...new Set(found)].find(isVisible) || null;
                })();
                scan.hasDialog = !!dialog;
                if (dialog) {
                    const dialogNodes = [];
                    const collectDialog = root => {
                        for (const el of root.querySelectorAll('button, [role="button"], a, [role="link"], input[type="button"], input[type="submit"]')) dialogNodes.push(el);
                        for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) collectDialog(el.shadowRoot); }
                    };
                    collectDialog(dialog);
                    const dialogButtons = [...new Set(dialogNodes)].filter(isVisible).filter(isInteractive);
                    const confirm = dialogButtons.find(node => {
                        if (node.disabled) return false;
                        const label = labelOf(node);
                        if (!label || skip.test(label)) return false;
                        return matchesAny(label, wanted(config.confirmRemoveLabels));
                    });
                    if (confirm) {
                        try { confirm.click(); return { state: 'waiting', key: null, action: 'remove-confirm', scan }; } catch (error) {}
                    }
                    // The QR/manual-key setup screen is also a dialog. It must
                    // continue through the manual-link and setup-key handling
                    // below. Only hold on a dialog that is clearly the existing
                    // authenticator-removal confirmation; otherwise this early
                    // return strands the flow at the barcode screen forever.
                    const dialogText = normalize(dialog.innerText || dialog.textContent || '');
                    const isRemovalDialog = /(remove|delete|quitar|eliminar|borrar|supprimer|retirer|entfernen|rimuovi|remover).{0,80}(authenticator|autenticador|authentificateur|application|app)/i.test(dialogText)
                        || /(authenticator|autenticador|authentificateur).{0,80}(remove|delete|quitar|eliminar|borrar|supprimer|retirer|entfernen|rimuovi|remover)/i.test(dialogText);
                    if (isRemovalDialog) return { state: 'waiting', key: null, action: 'dialog-open', scan };
                }

                // An authenticator is already configured (2-Step is on and the
                // management page lists it). The plan replaces it, so click the
                // row's Remove: Google exposes it as an icon button whose aria-label
                // names the authenticator ("Quitar el Autenticador de Google"), so
                // match remove labels that also mention the authenticator before
                // falling back to an edit/overflow affordance.
                const authInLabel = label => !!label && config.authNameTerms.some(term => label.includes(term));
                const dedicatedRemove = buttons.find(node => {
                    if (node.disabled) return false;
                    const label = labelOf(node);
                    if (!label || skip.test(label)) return false;
                    return authInLabel(label) && matchesAny(label, wanted(config.removeLabels));
                });
                // A setup dialog contains the word "authenticator" in its
                // title and instructions, but it is not an existing device
                // row. Do not let that copy route the observer back into the
                // remove/edit branch while the QR/manual-key flow is active.
                const hasExistingRow = !config.removed && !dialog && (!!dedicatedRemove || config.authNameTerms.some(term => haystack.includes(term)));
                scan.hasExistingRow = hasExistingRow;
                if (hasExistingRow) {
                    if (dedicatedRemove) {
                        try { dedicatedRemove.click(); return { state: 'waiting', key: null, action: 'remove', scan }; } catch (error) {}
                    }
                    const clicked = clickButton(config.removeLabels, skip);
                    if (clicked) return { state: 'waiting', key: null, action: 'remove', scan };
                    const opened = clickButton(config.rowEditLabels, skip);
                    if (opened) return { state: 'waiting', key: null, action: 'row-edit', scan };
                }

                // "I can't scan it" / "¿No se puede escanear?" /
                // "Ne peut-on pas le scanner ?" / "Can't it be scanned?" -- Google
                // phrase-varies the manual-key affordance across locales and
                // renders it as a plain span/div (jsaction/jsname/jscontroller on a
                // parent) far more often than as a real <button>. So besides the
                // exact label list, TREAT ANY visible clickable label that couples
                // a scan keyword with a cannot/not phrasing as the manual option,
                // then click the element that owns the handler.
                const manualPredicate = label => {
                    if (!label || skip.test(label)) return false;
                    if (matchesAny(label, wanted(config.manualLabels))) return true;
                    return /(scan|scann|escan)/i.test(label) && /(\\bcan\\s*'?\\s*t\\b|cannot|not scan|no se puede|no puedes|no puedo|ne peut|pas scanner|impossible|trouble|problem)/i.test(label);
                };
                // Google does not keep one stable element type for this link.
                // Depending on locale and rollout it can be an anchor, a
                // Material button, or a span/div whose event listener lives on
                // an ancestor (and has no role/jsaction attribute at all).
                // Keep the semantic ancestors first, but allow the visible
                // text node itself as a final fallback: click() bubbles to the
                // same handler and works across all three renderings.
                const manualClickable = 'button, [role="button"], a, [role="link"], [jsaction], [jscontroller], [jsname], [onclick], [tabindex]';
                let manualTarget = null;
                let manualTargetLabel = null;
                for (const part of textParts) {
                    const value = normalize(part.value);
                    if (!manualPredicate(value)) continue;
                    const clickable = (part.node.closest && part.node.closest(manualClickable)) || part.node;
                    if (!clickable || !isVisible(clickable)) continue;
                    manualTarget = clickable;
                    manualTargetLabel = value;
                    break;
                }
                if (!manualTarget) {
                    for (const node of buttons) {
                        const label = labelOf(node);
                        if (!manualPredicate(label)) continue;
                        manualTarget = node;
                        manualTargetLabel = label;
                        break;
                    }
                }
                let clickedManual = null;
                if (manualTarget) {
                    try {
                        manualTarget.scrollIntoView({ block: 'center', inline: 'center' });
                        manualTarget.click();
                        clickedManual = manualTargetLabel;
                    } catch (error) {}
                }
                scan.hasManual = !!clickedManual;
                if (clickedManual) return { state: 'waiting', key: null, action: 'manual', scan };

                const clickedStarted = clickButton(config.getStartedLabels, skip);
                scan.hasGetStarted = !!clickedStarted;
                if (clickedStarted) return { state: 'waiting', key: null, action: 'start', scan };

                const device = visible.find(node => node.matches && node.matches('[role="radio"], input[type="radio"]') && config.deviceLabels.some(term => labelOf(node).includes(term)));
                if (device) {
                    const pressed = device.checked === true || device.getAttribute('aria-checked') === 'true';
                    if (!pressed) { try { device.click(); } catch (error) {} }
                    clickButton(config.nextLabels, skip);
                    return { state: 'waiting', key: null, action: 'device', scan };
                }

                const authenticatorRadio = visible.find(node => node.matches && node.matches('[role="radio"], input[type="radio"]') && labelOf(node).includes('authenticator'));
                if (authenticatorRadio) {
                    const pressed = authenticatorRadio.checked === true || authenticatorRadio.getAttribute('aria-checked') === 'true';
                    if (!pressed) { try { authenticatorRadio.click(); } catch (error) {} }
                    clickButton(config.nextLabels, skip);
                    return { state: 'waiting', key: null, action: 'authenticator', scan };
                }

                if (config.haveKey && codeInputs.length === 0) {
                    const clicked = clickButton(config.confirmLabels, skip);
                    if (clicked) return { state: 'waiting', key: null, action: 'confirm', scan };
                }

                const next = clickButton(config.nextLabels, skip);
                if (next) return { state: 'waiting', key: null, action: 'next', scan };
                return { state: 'waiting', key: null, action: 'idle', scan };
            }""",
            {
                "current": current_password,
                "removed": bool(removed),
                "haveKey": bool(have_key),
                "codeSubmitted": bool(code_submitted),
                "getStartedLabels": _TWO_STEP_GET_STARTED_LABELS,
                "manualLabels": _TWO_STEP_MANUAL_LABELS,
                "removeLabels": _TWO_STEP_REMOVE_LABELS,
                "rowEditLabels": _TWO_STEP_ROW_EDIT_LABELS,
                "authNameTerms": _TWO_STEP_AUTH_NAME_TERMS,
                "confirmRemoveLabels": _TWO_STEP_CONFIRM_REMOVE_LABELS,
                "nextLabels": _TWO_STEP_AUTHENTICATOR_NEXT_LABELS,
                "deviceLabels": _TWO_STEP_DEVICE_LABELS,
                "confirmLabels": _TWO_STEP_CONFIRM_LABELS,
                "doneTerms": _TWO_STEP_DONE_TERMS,
            },
        )
        if isinstance(result, dict) and result.get("state") in {"done", "waiting", "error"}:
            return result
        return {"state": "waiting", "key": None, "action": None}
    except Exception:  # noqa: BLE001 - the next observer tick retries after a re-render
        return {"state": "waiting", "key": None, "action": None}


def _fill_two_step_code(page: "Page", secret: str) -> str:
    """Enter the TOTP matching ``secret`` into Google's setup verification box.

    Returns ``filled`` when the code was typed, ``submitted`` when a submit
    button was clicked, ``rejected`` when Google displayed an invalid-code error,
    or ``waiting`` when no code field is on screen yet.
    """
    try:
        code = generate_totp(secret)
    except Exception:  # noqa: BLE001
        return "waiting"
    try:
        result = page.evaluate(
            """config => {
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const isVisible = node => {
                    if (!node || !node.isConnected) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
                };
                const collect = (root, out) => {
                    for (const el of root.querySelectorAll('button, [role="button"], a, [role="link"], input')) out.push(el);
                    for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) collect(el.shadowRoot, out); }
                };
                const nodes = [];
                collect(document, nodes);
                const unique = [...new Set(nodes)];
                const visible = unique.filter(isVisible);
                const labelOf = node => normalize([node.innerText, node.textContent, node.getAttribute('aria-label'), node.getAttribute('value'), node.getAttribute('placeholder')].filter(Boolean).join(' '));
                const buttons = visible.filter(node => node.matches && node.matches('button, [role="button"], a, [role="link"], input[type="button"], input[type="submit"]'));
                const wanted = list => list.map(normalize).filter(Boolean);
                const matchesAny = (label, list) => list.some(term => label === term || label.startsWith(term + ' ') || label.endsWith(' ' + term));
                let textParts = [];
                for (const node of unique) {
                    if (node.shadowRoot) continue;
                    if (node.childElementCount > 0) continue;
                    if (!isVisible(node)) continue;
                    const value = (node.innerText || node.textContent || '').trim();
                    if (value) textParts.push(value);
                }
                const haystack = normalize(textParts.join(' \\n ')) + ' \\n ' + normalize(document.body && document.body.innerText || '');
                if (config.rejectionTerms.some(term => haystack.includes(term))) return 'rejected';
                const isCodeField = node => {
                    if (!node.matches || !node.matches('input')) return false;
                    const label = labelOf(node);
                    return node.matches('input[autocomplete="one-time-code"], input[type="tel"], input[inputmode="numeric"]')
                        || /(code|codigo|verification|verificacion|verifier|codice|verifizierung|digit|digito|chiffre)/i.test(label);
                };
                const inputs = visible.filter(isCodeField);
                if (!inputs.length) return 'waiting';
                const input = inputs[0];
                if (!input.value) {
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    setter.call(input, config.code);
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }
                const submit = buttons.find(node => {
                    if (node.disabled) return false;
                    const label = labelOf(node);
                    if (!label || /(forgot|oubli|olvid|cancel|annul|canc|back|retour)/.test(label)) return false;
                    return matchesAny(label, wanted(config.submitLabels));
                });
                if (submit) { submit.click(); return 'submitted'; }
                return 'filled';
            }""",
            {"code": code, "rejectionTerms": _OTP_REJECTION_TERMS, "submitLabels": ("next", "verify", "add", "continue", "done", "turn on", "suivant", "verifier", "verificar", "activar", "agregar", "añadir", "ajouter", "confirmar", "confirmer", "terminer")},
        )
        return result if result in {"filled", "submitted", "rejected", "waiting"} else "waiting"
    except Exception:  # noqa: BLE001 - the next observer tick retries after a re-render
        return "waiting"


def _drive_recovery_page(page: "Page", new_email: str, current_password: str | None = None, already_saved: bool = False) -> dict[str, object]:
    """Drive Google's recovery-email page to ``new_email``.

    Opens ``https://myaccount.google.com/recovery/email``, reveals the recovery
    address form (behind Google's Edit affordance), replaces the address with the
    plan's rotated pool address and saves it. Returns
    ``{"state": "done"|"waiting"|"error", "action": str|None}``.
    """
    try:
        result = page.evaluate(
            """config => {
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const isVisible = node => {
                    if (!node || !node.isConnected) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
                };
                const collect = (root, out) => {
                    for (const el of root.querySelectorAll('button, [role="button"], a, [role="link"], input')) out.push(el);
                    for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) collect(el.shadowRoot, out); }
                };
                const nodes = [];
                collect(document, nodes);
                const unique = [...new Set(nodes)];
                const visible = unique.filter(isVisible);
                const labelOf = node => normalize([node.innerText, node.textContent, node.getAttribute('aria-label'), node.getAttribute('value'), node.getAttribute('placeholder')].filter(Boolean).join(' '));
                const buttons = visible.filter(node => node.matches && node.matches('button, [role="button"], a, [role="link"], input[type="button"], input[type="submit"]'));
                const wanted = list => list.map(normalize).filter(Boolean);
                const matchesAny = (label, list) => list.some(term => label === term || label.startsWith(term + ' ') || label.endsWith(' ' + term));
                const skip = /(forgot|oubli|olvid|cancel|annul|canc|back|retour|atras|atrás)/;
                const clickButton = (list) => {
                    const targets = wanted(list);
                    for (const node of buttons) {
                        if (node.disabled) continue;
                        const label = labelOf(node);
                        if (!label) continue;
                        if (skip.test(label)) continue;
                        if (matchesAny(label, targets)) { try { node.click(); return label; } catch (error) {} }
                    }
                    return null;
                };
                const bodyText = normalize(document.body && document.body.innerText || '');
                if (config.errorTerms.some(term => bodyText.includes(normalize(term)))) return { state: 'error', action: 'error' };
                if (config.successTerms.some(term => bodyText.includes(normalize(term)))) return { state: 'done', action: 'success' };

                // Google may re-authenticate before the recovery page loads.
                const passwordFields = visible.filter(node => node.matches && node.matches('input[type="password"]'));
                if (passwordFields.length) {
                    const field = passwordFields[0];
                    if (config.current && !field.value) {
                        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                        setter.call(field, config.current);
                        field.dispatchEvent(new Event('input', { bubbles: true }));
                        field.dispatchEvent(new Event('change', { bubbles: true }));
                        return { state: 'waiting', action: 'password-filled' };
                    }
                    const clicked = config.current ? clickButton(config.nextLabels) : null;
                    if (!clicked && config.current) return { state: 'waiting', action: 'password-keyboard' };
                    return { state: 'waiting', action: clicked ? 'password-submitted' : 'password' };
                }

                const emailFields = visible.filter(node => node.matches && node.matches('input[type="email"], input[name*="email" i], input[type="text"][name*="mail" i]'));
                const target = normalize(config.email);
                if (emailFields.length) {
                    const field = emailFields[0];
                    if (normalize(field.value) !== target) {
                        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                        setter.call(field, config.email);
                        field.dispatchEvent(new Event('input', { bubbles: true }));
                        field.dispatchEvent(new Event('change', { bubbles: true }));
                        return { state: 'waiting', action: 'email-filled' };
                    }
                    // We already clicked Save on a previous tick and Google kept
                    // the target address: never click Edit again (that re-opened
                    // the form after a successful change).
                    if (config.alreadySaved) return { state: 'done', action: 'saved' };
                    const saved = clickButton(config.saveLabels);
                    if (saved) return { state: 'waiting', action: 'save' };
                    return { state: 'waiting', action: 'save-ready' };
                }

                if (config.alreadySaved) return { state: 'done', action: 'saved' };
                const opened = clickButton(config.editLabels);
                if (opened) return { state: 'waiting', action: 'edit' };
                return { state: 'waiting', action: 'idle' };
            }""",
            {
                "email": new_email,
                "current": current_password,
                "alreadySaved": already_saved,
                "editLabels": _RECOVERY_EDIT_LABELS,
                "saveLabels": _RECOVERY_SAVE_LABELS,
                "nextLabels": _TWO_STEP_AUTHENTICATOR_NEXT_LABELS,
                "successTerms": _RECOVERY_SUCCESS_TERMS,
                "errorTerms": _RECOVERY_ERROR_TERMS,
            },
        )
        if isinstance(result, dict) and result.get("state") in {"done", "waiting", "error"}:
            return result
        return {"state": "waiting", "action": None}
    except Exception:  # noqa: BLE001 - the next observer tick retries after a re-render
        return {"state": "waiting", "action": None}


def _credential_change_is_confirmed(observer: "BrowserObserver", material: dict[str, object]) -> bool:
    """Only a fully-confirmed change may be persisted.

    A plan is confirmed when every requested field was verified inside Google:
    the password only after Google's explicit success banner, and 2FA only after
    the authenticator setup finished AND Google's own pairing key was captured.
    Anything else must fail without touching the credential vault (the operator's
    manual confirm remains available for those plans).
    """
    if material.get("password_requested") and not observer._password_change_confirmed:
        return False
    if material.get("recovery_requested") and not observer._recovery_change_confirmed:
        return False
    if material.get("two_fa_requested"):
        if not observer._two_step_done:
            return False
        if not observer._two_step_key:
            return False
    return True


def _complete_credential_change(observer: "BrowserObserver", page: "Page", material: dict[str, object], detail: str) -> None:
    if not _credential_change_is_confirmed(observer, material):
        observer._emit(
            "credential_change.failed",
            "Google did not confirm the complete credential change, so the new password was NOT saved; review the session and confirm the plan manually if it actually succeeded",
            result="failure",
        )
        observer._stop.set()
        return
    _write_credential_sheets(observer, material)
    observer._emit("credential_change.files_saved", "the new password and 2FA secret were written into the profile directory")
    observer._emit("credential_change.completed", detail)
    observer._stop.set()


def _finish_password_change(observer: "BrowserObserver", page: "Page", material: dict[str, object]) -> None:
    """Conclude the password step and hand off to the optional recovery / 2-Step legs.

    Only reached after Google's success banner confirmed the change, so the new
    password may be written to the vault once every requested field completes.
    """
    observer._password_change_confirmed = True
    observer._emit("password.change_completed", "Google confirmed the password change; the new password is being persisted independently of the authenticator leg")
    two_step_requested = bool(material.get("two_fa_requested") or material.get("2fa"))
    _write_credential_sheets(observer, material, include_two_fa=not two_step_requested)
    if two_step_requested or material.get("recovery_requested"):
        # Remaining legs (recovery e-mail, then authenticator) are driven by the
        # next observer ticks, which route each leg to its own Google page.
        return
    _complete_credential_change(observer, page, material, "the new password was applied inside the saved Google session")


def _confirm_password_change_state(page: "Page", submitted: bool = True) -> str:
    """Detect the outcome after the password-change submit was clicked.

    Runs on any My Account hand-off page so a successful change is recognised
    even when Google keeps the filled form on screen or redirects away from the
    password page. Returns ``done`` (explicit success), ``error`` (rejected),
    ``likely`` (submitted and the form is gone with no error, still settling) or
    ``pending`` (still rendering).
    """
    try:
        result = page.evaluate(
            """config => {
                const normalize = value => (value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const isVisible = node => {
                    if (!node || !node.isConnected) return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0' && rect.width > 0 && rect.height > 0;
                };
                const collect = (root, out) => {
                    for (const el of root.querySelectorAll('input[type="password"]')) out.push(el);
                    for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) collect(el.shadowRoot, out); }
                };
                const nodes = [];
                collect(document, nodes);
                const fields = [...new Set(nodes.filter(node => node.matches && node.matches('input[type="password"]') && isVisible(node)))];
                // Google renders the confirmation either inline or as a toast /
                // live-region, sometimes inside a shadow root, and the text is
                // localized. Collect every text surface before matching.
                const parts = [];
                const pushText = node => { if (node) { const text = node.innerText || node.textContent; if (text) parts.push(text); } };
                pushText(document.body);
                for (const node of document.querySelectorAll('[role="status"], [role="alert"], [role="dialog"], [aria-live]')) pushText(node);
                const shadowText = root => {
                    for (const el of root.querySelectorAll('*')) {
                        if (el.shadowRoot) { pushText(el.shadowRoot); shadowText(el.shadowRoot); }
                    }
                };
                shadowText(document);
                const text = normalize(parts.join(' '));
                const successRe = new RegExp(config.successRe);
                if (/(something went wrong|couldn.t (change|update)|unable to (change|update)|wrong password|your password may not be correct|an error occurred|se produjo un error|no se pudo (cambiar|actualizar|modificar)|ocurrio un error|intenta (de )?nuevo)/.test(text)) {
                    return 'error';
                }
                if (successRe.test(text)) {
                    return 'done';
                }
                // The explicit banner may be a transient toast that a slow tick
                // misses. Once the submit is in flight and Google has dropped the
                // password form without any error, treat it as settled: the
                // caller waits a few ticks before accepting so a navigation gap
                // cannot be mistaken for success.
                if (config.submitted && !fields.length) {
                    return 'likely';
                }
                return 'pending';
            }""",
            {"successRe": _PASSWORD_CHANGE_SUCCESS_JS_RE, "submitted": bool(submitted)},
        )
        if result in {"done", "error", "likely", "pending"}:
            return result
        return "pending"
    except Exception:  # noqa: BLE001 - the next observer tick retries after a re-render
        return "pending"


def _bump_password_confirm(observer: "BrowserObserver") -> None:
    observer._password_change_confirm_ticks = getattr(observer, "_password_change_confirm_ticks", 0) + 1
    if observer._password_change_confirm_ticks > 30:
        observer._emit("credential_change.failed", "the password-change submit could not be confirmed inside Google", result="failure")
        observer._stop.set()


def _confirm_in_flight_password_change(observer: "BrowserObserver", page: "Page", material: dict) -> None:
    """Resolve a password submit that already left the form.

    Runs on any host (including the sign-in hosts Google briefly lands on) so a
    successful change is never lost when the confirmation pages on a login host.
    Never re-drives the form: at most it re-authenticates with the freshly
    provisioned password and waits for the next tick.
    """
    outcome = _confirm_password_change_state(page)
    if outcome == "error":
        observer._emit("credential_change.failed", "Google rejected the submitted password change (check the current password and password policy)", result="failure")
        observer._stop.set()
        return
    if outcome == "done":
        _finish_password_change(observer, page, material)
        return
    if outcome == "likely":
        # No explicit banner (missed a transient toast / localized text) but the
        # form is gone with no error. Accept after a short settle so we never
        # lose a password Google actually changed.
        observer._password_change_settle_ticks = getattr(observer, "_password_change_settle_ticks", 0) + 1
        if observer._password_change_settle_ticks >= 4:
            _finish_password_change(observer, page, material)
        else:
            _bump_password_confirm(observer)
        return
    # Still rendering, or Google signed the session out to a login host. Prefer
    # the new password so a re-auth does not loop on the stale old credential.
    url = page.url or ""
    host = urlparse(url).netloc.lower()
    if host in _LOGIN_HOSTS:
        new_password = str(material.get("password") or "")
        existing = getattr(observer, "_credentials", None)
        if new_password and (existing is None or existing[1] != new_password):
            identifier = existing[0] if existing else None
            observer._credentials = (identifier, new_password)
            # Pin the value so _resolve_credentials does not overwrite it with
            # the stale stored password, and clear the per-URL guards so the
            # login host can be re-submitted once with the new password.
            observer._credentials_tried = True
            for bag in ("_auto_attempted_urls", "_submitted_urls", "_submitted_credential_stages"):
                collection = getattr(observer, bag, None)
                if collection is not None:
                    discard = getattr(collection, "discard", None)
                    if discard is not None:
                        discard(url)
                    if bag == "_submitted_credential_stages":
                        collection.difference_update({key for key in collection if key.startswith(f"{url}#")})
    _bump_password_confirm(observer)


def _report_two_step_scan(observer: "BrowserObserver", outcome: dict) -> None:
    """Surface what the Two-Step scanner detected so a stall is visible instead
    of a silent freeze (and the next run can be debugged from the board)."""
    scan = outcome.get("scan")
    if not isinstance(scan, dict):
        return
    parts = [name[3:].replace("has", "").lower().replace("_", " ") for name, on in sorted(scan.items()) if isinstance(on, bool) and on]
    label = (" ".join(parts) or "empty-page") + f" | action={str(outcome.get('action') or 'none')}"
    if getattr(observer, "_last_two_step_scan", None) == label:
        return
    observer._last_two_step_scan = label
    observer._emit("two_step.scan", label)


def _handle_credential_change_tick(observer: "BrowserObserver", page: "Page", host: str) -> None:
    """Drive the Google ``signinoptions/password`` change and optional 2-Step setup.

    The login / TOTP re-auth Google may require before reaching My Account is
    handled by the shared auto-login hooks above, so this only acts on the
    already-authenticated ``myaccount.google.com`` surface.
    """
    observer._credential_tick_count = getattr(observer, "_credential_tick_count", 0) + 1
    if observer._credential_tick_count > 240:
        observer._emit("credential_change.failed", "the automated credential change did not complete within the tick budget", result="failure")
        observer._stop.set()
        return
    material = _resolve_credential_change_material(observer)
    url = page.url or ""
    path = urlparse(url).path.lower()

    password_requested = bool(material.get("password_requested"))
    recovery_requested = bool(material.get("recovery_requested"))
    two_fa_requested = bool(material.get("two_fa_requested"))
    current_password = _observer_current_password(observer)

    # --- Leg 1 (in flight): confirm a submitted password change --------------
    # Google can render the "Password changed" confirmation on a login host
    # (accounts.google.com) before it bounces the browser back, so this runs
    # before the host guards. Re-driving the form would re-submit the password.
    if password_requested and observer._password_change_clicked and not observer._password_change_confirmed:
        _confirm_in_flight_password_change(observer, page, material)
        return

    if host in _LOGIN_HOSTS:
        return
    if host not in _ACCOUNT_HANDOFF_HOSTS:
        return

    # --- Leg 1: password (drive) ---------------------------------------------
    if password_requested and not observer._password_change_confirmed:
        if not path.startswith(_CREDENTIAL_CHANGE_PAGE_PATH):
            _navigate_settings(page, _CREDENTIAL_CHANGE_URL)
            return
        new_password = material.get("password")
        if not new_password:
            observer._emit("credential_change.failed", "no automated password material was provisioned for this session", result="failure")
            observer._stop.set()
            return
        if current_password is None:
            observer._emit("credential_change.failed", "no stored account password is available to fill the current-password step", result="failure")
            observer._stop.set()
            return
        stage = getattr(observer, "_credential_change_stage", 0)
        state = _drive_password_change_form(page, current_password, str(new_password), stage, observer._password_change_clicked)
        if state == "error":
            observer._emit("credential_change.failed", "Google rejected the password-change form before submission", result="failure")
            observer._stop.set()
        elif state == "done":
            _finish_password_change(observer, page, material)
        elif state == "keyboard":
            _press_enter_on_password(page)
        elif state == "keyboard2":
            # The final new-password submit went through Enter, not a click:
            # mark it submitted so the next ticks confirm the change instead of
            # re-driving (and re-submitting) the form forever.
            _press_enter_on_password(page)
            observer._credential_change_stage = 2
            observer._password_change_clicked = True
            observer._password_change_confirm_ticks = 0
            observer._emit("password.change_submitted", "the new password was filled and submitted with Enter")
        elif state == "submitted1":
            observer._credential_change_stage = 1
            observer._emit("password.change_submitted", "the current-password step was filled and submitted")
        elif state == "submitted2":
            observer._credential_change_stage = 2
            observer._password_change_clicked = True
            observer._password_change_confirm_ticks = 0
            observer._emit("password.change_submitted", "the new password was filled and submitted")
        return

    # --- Leg 2: recovery e-mail ----------------------------------------------
    if recovery_requested and not observer._recovery_change_confirmed:
        if not any(path.startswith(prefix) for prefix in _RECOVERY_PAGE_PATHS):
            _navigate_settings(page, _RECOVERY_URL)
            return
        new_recovery = str(material.get("recovery") or "").strip()
        if not new_recovery:
            observer._emit("credential_change.failed", "no automated recovery address was provisioned for this session", result="failure")
            observer._stop.set()
            return
        outcome = _drive_recovery_page(page, new_recovery, current_password, already_saved=observer._recovery_saved)
        action = str(outcome.get("action") or "")
        if action == "password-keyboard":
            _press_enter_on_password(page)
        if action == "save":
            # Remember the submit so a slow Google confirmation cannot make the
            # next tick click Edit and re-open the recovery form.
            observer._recovery_saved = True
        if outcome.get("state") == "error":
            observer._emit("credential_change.failed", "Google rejected the recovery-email update", result="failure")
            observer._stop.set()
            return
        if outcome.get("state") == "done":
            observer._recovery_change_confirmed = True
            observer._emit("recovery.change_completed", "Google confirmed the recovery email update")
            return
        if observer._credential_tick_count > 200:
            observer._emit("credential_change.failed", "the recovery email was not updated inside Google, so the credentials were NOT saved; confirm the plan manually if it actually succeeded", result="failure")
            observer._stop.set()
            return
        return

    # --- Leg 3: Two-Step Verification authenticator --------------------------
    if two_fa_requested and not observer._two_step_done:
        if not any(path.startswith(prefix) for prefix in _TWO_STEP_PAGE_PATHS):
            # A 2FA-only plan must never run the password branch: Google's own
            # redirects can land it on the password surface, so route it to the
            # authenticator page instead of failing on missing password material.
            _navigate_settings(page, _TWO_STEP_URL)
            return
        if not material.get("2fa"):
            observer._emit("credential_change.failed", "the Two-Step Verification flow started without a 2FA secret; nothing was persisted", result="failure")
            observer._stop.set()
            return
        outcome = _drive_two_step_setup(
            page,
            current_password,
            removed=observer._two_step_removed,
            have_key=bool(observer._two_step_key),
            code_submitted=observer._two_step_code_submitted,
        )
        action = str(outcome.get("action") or "")
        _report_two_step_scan(observer, outcome)
        if action == "password-keyboard":
            # Google's re-auth prompt stayed filled but is not submitting:
            # press the real Enter key on the password field.
            _press_enter_on_password(page)
        if action in {"remove", "remove-confirm"}:
            observer._two_step_removed = True
        captured = outcome.get("key")
        if captured:
            key = str(captured).strip().upper()
            if key and key != observer._two_step_key:
                observer._two_step_key = key
                observer._two_step_sheet_written = False
                stored = dict(material)
                stored["2fa"] = key
                material = stored
                if observer._store_credential_change is not None:
                    try:
                        observer._store_credential_change(observer.session_id, stored)
                        logger.info("session %s: stored Google's pairing secret from the 2SV setup screen", observer.session_id)
                    except Exception:  # noqa: BLE001
                        logger.exception("could not store the 2SV pairing secret for session %s", observer.session_id)
                observer._emit("two_step.secret_captured", "Google's authenticator pairing key was captured and replaced the generated secret")
        if outcome.get("state") == "done":
            observer._two_step_done = True
            _complete_credential_change(observer, page, material, "the password was changed and the authenticator was added in Two-Step Verification; the secret was saved to the profile")
            return
        if outcome.get("state") == "error":
            observer._emit("credential_change.failed", "Google rejected the new authenticator verification code", result="failure")
            observer._stop.set()
            return
        if observer._two_step_code_submitted:
            # The OTP was accepted by the page and the final confirmation is
            # still rendering. Never re-enter setup, remove the device, or
            # click Verify again while Google settles the result.
            return
        if action == "reauth-code":
            secret = _resolve_otp_secret(observer)
            if secret:
                status = _fill_two_step_code(page, secret)
                if status in {"submitted", "rejected"}:
                    observer._two_step_code_attempts += 1
                    if status == "rejected" and observer._two_step_code_attempts > 5:
                        observer._emit("credential_change.failed", "Google rejected the account-verification code repeatedly", result="failure")
                        observer._stop.set()
                        return
        if observer._two_step_key:
            status = _fill_two_step_code(page, observer._two_step_key)
            if status in {"submitted", "rejected"}:
                observer._two_step_code_attempts += 1
                if status == "submitted":
                    observer._two_step_code_submitted = True
                    observer._emit("two_step.code_submitted", "the OTP generated from the new authenticator secret was submitted to Google")
                if status == "rejected" and observer._two_step_code_attempts > 5:
                    observer._emit("credential_change.failed", "Google rejected the authenticator verification code repeatedly", result="failure")
                    observer._stop.set()
                    return
        if observer._credential_tick_count > 200:
            observer._emit("credential_change.failed", "Two-Step Verification did not finish inside Google, so the new credentials were NOT saved; confirm the plan manually if the authenticator was actually added", result="failure")
            observer._stop.set()
            return
        return

    # --- Every requested leg is finished -------------------------------------
    if password_requested or recovery_requested or two_fa_requested:
        _complete_credential_change(observer, page, material, "the requested credential change was applied inside the saved Google session")
        return


def _press_enter_on_password(page: "Page") -> None:
    """Real-keyboard Enter fallback for Google password prompts.

    Google sometimes renders the submit of a re-auth / password step as an
    unlabelled icon button that no label match finds. A synthetic JS Enter does
    not submit the page, so the observer sends a real Enter key event; Google
    treats it exactly like clicking Next.
    """
    try:
        field = page.locator("input[type=password]:visible, input[name=Passwd]:visible, input#password:visible").first
        if field.count():
            field.focus(timeout=1500)
            field.press("Enter", timeout=1500)
            return
    except Exception:  # noqa: BLE001 - fall back to a plain key press
        pass
    try:
        page.keyboard.press("Enter")
    except Exception:  # noqa: BLE001
        pass


def _navigate_settings(page: "Page", url: str) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception:  # noqa: BLE001 - the next observer tick retries navigation
        logger.debug("Gmail settings navigation is still loading", exc_info=True)


def _set_importance_markers(page: "Page") -> str | None:
    """Select the localized Show markers radio, or confirm it is selected."""
    try:
        result = page.evaluate(
            """config => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const sectionTerms = config.section;
                const showTerms = config.show;
                const noTerms = config.no;
                const radioNodes = [...document.querySelectorAll('input[type="radio"], [role="radio"]')];
                for (const node of radioNodes) {
                    const option = node.closest('label, tr, [role="radio"], [role="row"]') || node.parentElement;
                    const optionText = normalize(option?.innerText || node.getAttribute('aria-label'));
                    if (!showTerms.some(term => optionText.includes(term))) continue;
                    const scope = option?.closest('tr, [role="row"], [role="group"]') || option?.parentElement;
                    const scopeText = normalize(scope?.innerText || optionText);
                    if (!sectionTerms.some(term => scopeText.includes(term)) && !sectionTerms.some(term => document.body.innerText.toLowerCase().includes(term))) continue;
                    if (noTerms.some(term => optionText.includes(term))) continue;
                    const checked = node.checked === true || node.getAttribute('aria-checked') === 'true';
                    if (checked) return 'already';
                    node.click();
                    return 'clicked';
                }
                return null;
            }""",
            {"section": _GMAIL_MARKER_SECTION_LABELS, "show": _GMAIL_SHOW_MARKER_LABELS, "no": _GMAIL_NO_MARKER_LABELS},
        )
        return result if result in {"clicked", "already"} else None
    except Exception:  # noqa: BLE001 - fallback scans visible controls below
        return _gmail_click_option_text(page, _GMAIL_SHOW_MARKER_LABELS, _GMAIL_NO_MARKER_LABELS)


def _show_spam_system_label(page: "Page") -> str | None:
    """Show Spam in the System labels row in any supported locale."""
    try:
        result = page.evaluate(
            """config => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const showTerms = config.show;
                const hideTerms = config.hide;
                const spamNodes = [...document.querySelectorAll('td, [role="cell"], span, div')].filter(node => normalize(node.innerText || node.textContent) === 'spam');
                for (const spam of spamNodes) {
                    const row = spam.closest('tr, [role="row"]');
                    if (!row) continue;
                    const controls = [...row.querySelectorAll('a, button, [role="button"], [role="link"]')];
                    const show = controls.find(control => showTerms.includes(normalize(control.innerText || control.getAttribute('aria-label'))));
                    if (show) { show.click(); return 'clicked'; }
                    if (controls.some(control => hideTerms.includes(normalize(control.innerText || control.getAttribute('aria-label'))))) return 'already';
                }
                return null;
            }""",
            {"show": _GMAIL_SHOW_LABELS, "hide": _GMAIL_HIDE_LABELS},
        )
        return result if result in {"clicked", "already"} else None
    except Exception:  # noqa: BLE001 - fallback scans visible row controls below
        return _gmail_click_spam_row_fallback(page)


def _gmail_click_option_text(page: "Page", wanted: tuple[str, ...], excluded: tuple[str, ...] = ()) -> str | None:
    try:
        for candidate in page.locator('label, [role="radio"], input[type="radio"]').all():
            text = (candidate.inner_text() or candidate.get_attribute("aria-label") or "").strip().lower()
            if any(term in text for term in wanted) and not any(term in text for term in excluded):
                candidate.click(timeout=1500)
                return "clicked"
    except Exception:  # noqa: BLE001
        return None
    return None


def _gmail_click_spam_row_fallback(page: "Page") -> str | None:
    try:
        for row in page.locator('tr, [role="row"]').all():
            row_text = (row.inner_text() or "").strip().lower()
            if re.search(r"\bspam\b", row_text):
                controls = row.locator('a, button, [role="button"], [role="link"]').all()
                for control in controls:
                    text = (control.inner_text() or control.get_attribute("aria-label") or "").strip().lower()
                    if text in _GMAIL_SHOW_LABELS:
                        control.click(timeout=1500)
                        return "clicked"
                    if text in _GMAIL_HIDE_LABELS:
                        return "already"
    except Exception:  # noqa: BLE001
        return None
    return None


def _gmail_click_save_changes(page: "Page") -> bool:
    try:
        return _contacts_click_label(page, _GMAIL_SAVE_LABELS)
    except Exception:  # noqa: BLE001 - settings may auto-save in some builds
        return False


def _contacts_advance_file_flow(observer: "BrowserObserver", page: "Page") -> None:
    """Progress the import toward the file upload: open the importer when it
    is closed, then pick a file when the importer is asking for one."""
    if not observer._contacts_open_clicked:
        if _contacts_click_label(page, _CONTACTS_IMPORT_LABELS):
            observer._contacts_open_clicked = True
            observer._emit("contacts.dialog_opened", "contacts import dialog opened")
            return
        if observer._contacts_tick_count >= 4:
            # The import entry can sit under the settings menu in recent UIs;
            # open settings once, then retry the import label next tick.
            if _contacts_click_label(page, _CONTACTS_SETTINGS_LABELS):
                observer._emit("contacts.settings_opened", "opened contacts settings menu")
        return
    if not observer.contacts_csv_path or not Path(observer.contacts_csv_path).exists():
        observer._emit("contacts.failed", "upload CSV file is missing from disk", result="failure")
        observer._stop.set()
        return
    try:
        file_input = page.locator("input[type=file]").first
        if file_input.count():
            observer._contacts_uploaded = True
            observer._contacts_upload_tick = observer._contacts_tick_count
            file_input.set_input_files(observer.contacts_csv_path)
            observer._emit("contacts.file_uploaded", "uploaded contacts CSV for import")
            return
    except Exception:  # noqa: BLE001
        pass
    _contacts_click_label(page, _CONTACTS_FILE_BUTTON_LABELS)


def _contacts_click_final_import(page: "Page") -> bool:
    """Click the exact final Google Contacts import button when available.

    The selector is intentionally executed through the page's own
    ``document.querySelector(...).click()`` path because this is the control
    Google renders for the final action. A locator click remains a compatibility
    fallback for test doubles and alternate Playwright page implementations.
    """
    try:
        clicked = page.evaluate(
            """selector => {
                const button = document.querySelector(selector);
                if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') {
                    return false;
                }
                button.click();
                return true;
            }""",
            _CONTACTS_FINAL_IMPORT_SELECTOR,
        )
        if clicked:
            return True
    except Exception:  # noqa: BLE001 - use locator fallback below
        pass
    try:
        candidate = page.locator(_CONTACTS_FINAL_IMPORT_SELECTOR).first
        if candidate.count() and candidate.is_visible():
            candidate.click(timeout=1500)
            return True
    except Exception:  # noqa: BLE001 - use localized text fallback below
        return False
    return False


def _contacts_click_label(page: "Page", labels: tuple[str, ...]) -> bool:
    """Click the visible localized label itself, then use an element fallback.

    Google renders the final Contacts action as a text label inside a
    clickable control. Targeting the exact text first avoids accidentally
    selecting the dialog title (for example, ``Importar contactos`` when the
    action is ``Importar``). The semantic-control scan remains a fallback for
    builds where the label is not exposed as a separate text node.
    """
    wanted = tuple(dict.fromkeys(label.strip() for label in labels if label.strip()))

    # Prefer the literal visible action text. The regex keeps matching
    # case-insensitive while retaining exact text boundaries and whitespace
    # normalization, and the clickable-ancestor check excludes dialog titles.
    try:
        get_by_text = getattr(page, "get_by_text")
        for label in wanted:
            text_locator = get_by_text(re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE))
            for candidate in text_locator.all():
                try:
                    if not candidate.is_visible():
                        continue
                    is_clickable = candidate.evaluate(
                        """el => Boolean(el.closest(
                            'button, a, [role="button"], [role="menuitem"],
                            [role="tab"], input[type="button"], input[type="submit"],
                            [tabindex]:not([tabindex="-1"])
                        ))"""
                    )
                    if not is_clickable:
                        continue
                    candidate.click(timeout=1500)
                    return True
                except Exception:  # noqa: BLE001 - keep scanning visible matches
                    continue
    except Exception:  # noqa: BLE001 - older/fake pages may not expose get_by_text
        pass

    # Fallback for Google builds that expose only the surrounding control.
    try:
        for candidate in page.locator("button, input[type=button], input[type=submit], [role=button], [role=menuitem], [role=tab], a, li").all():
            try:
                label = (candidate.inner_text() or "").strip().lower()
            except Exception:  # noqa: BLE001
                continue
            if any(w.lower() in label[:120] for w in wanted):
                try:
                    if not candidate.is_visible():
                        continue
                    candidate.click(timeout=1500)
                    return True
                except Exception:  # noqa: BLE001 - hidden/disabled; keep scanning
                    continue
    except Exception:  # noqa: BLE001
        return False
    return False


def _contacts_dialog_closed_after_confirm(observer: "BrowserObserver", page: "Page") -> bool:
    """Treat a disappeared import dialog as success after the click settles.

    Google often displays the success toast only briefly (and it may not be
    exposed through Playwright's visible-text snapshot). Requiring two ticks
    after the exact action-text click and confirming that no import dialog or
    final action remains prevents a click acknowledgement from being mistaken
    for a completed import.
    """
    if not observer._contacts_confirm_clicked or observer._contacts_post_confirm_ticks < 2:
        return False
    if _contacts_import_dialog_visible(page):
        return False
    return not _contacts_has_visible_exact_text(page, ("import", "importar", "importer"))


def _contacts_import_result_route(observer: "BrowserObserver", page: "Page") -> bool:
    """Recognize Google's durable post-import route when the toast is gone.

    The completion toast is transient and is not consistently exposed through
    Playwright's accessible body text. After the final Import action succeeds,
    Google navigates to the imported label (``/label/<id>``). Requiring the
    confirmation click and a short settle window makes this route a safe,
    durable completion signal without treating the initial Contacts page as
    success.
    """
    if not observer._contacts_confirm_clicked or observer._contacts_post_confirm_ticks < 2:
        return False
    try:
        parsed = urlparse(page.url or "")
    except Exception:  # noqa: BLE001 - a closing page is not a success signal
        return False
    if parsed.hostname != "contacts.google.com":
        return False
    return bool(re.fullmatch(r"/label/[^/]+/?", parsed.path or ""))


def _contacts_import_dialog_visible(page: "Page") -> bool:
    try:
        for selector in ('[role="dialog"]', '[aria-modal="true"]'):
            for dialog in page.locator(selector).all():
                try:
                    if dialog.is_visible():
                        return True
                except Exception:  # noqa: BLE001 - continue checking other dialog candidates
                    continue
    except Exception:  # noqa: BLE001 - fall back to the exact action check
        pass
    return False


def _contacts_has_visible_exact_text(page: "Page", labels: tuple[str, ...]) -> bool:
    try:
        get_by_text = getattr(page, "get_by_text")
        for label in labels:
            locator = get_by_text(re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE))
            for candidate in locator.all():
                try:
                    if candidate.is_visible():
                        return True
                except Exception:  # noqa: BLE001 - keep scanning matches
                    continue
    except Exception:  # noqa: BLE001 - old/fake pages do not expose get_by_text
        return False
    return False


def _visible_text_mentions_done(page: "Page") -> bool:
    text = _visible_text(page).lower()
    return any(marker in text for marker in _CONTACTS_DONE_MARKERS)

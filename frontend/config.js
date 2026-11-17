/*
 * Adresse de l'API (backend).
 *
 *  - Vide ("") : le frontend appelle l'API sur sa propre adresse. C'est le cas quand le backend
 *    FastAPI sert aussi les pages (développement local : http://127.0.0.1:8000).
 *  - Frontend et backend hébergés séparément (ex. Netlify + Render) : mettre l'URL du backend,
 *    sans barre finale, par exemple "https://upb-api.onrender.com".
 *
 * Ce fichier est public : n'y mettez jamais de clé, de mot de passe ni de jeton.
 */
window.UPB_API_URL = "";

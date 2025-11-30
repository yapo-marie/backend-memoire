## Backend (Express)

### Configuration

1. Dupliquez `.env.example` vers `.env` puis renseignez vos valeurs :
   ```bash
   cp .env.example .env
   ```
   Champs requis :
   - `FIREBASE_DATABASE_URL` (obligatoire pour accéder à Firebase via l'API)
   - `FIREBASE_DATABASE_SECRET` si votre Realtime Database exige un token legacy
   - `CLIENT_ORIGIN` (URL du frontend autorisée par CORS)
   - `NEXT_PUBLIC_API_URL` côté frontend doit cibler le `PORT` défini ici (par défaut `4000`).

2. Installez les dépendances :
   ```bash
   npm install
   ```

3. Démarrez l'API :
   ```bash
   npm run dev
   ```

L’API charge automatiquement le fichier `.env` grâce à `dotenv`. Toute requête du frontend (properties/tenants/payments/auth) doit passer par cette couche pour atteindre Firebase/Stripe.

### Routes principales

- `POST /api/payments/checkout` : crée une session Stripe Checkout en ajoutant les métadonnées locataire/propriété (utilisé par le frontend pour envoyer un lien de paiement).
- `GET /api/payments/history?ownerId=<id>` : renvoie les dernières sessions Checkout filtrées par bailleur/locataire pour déterminer si un paiement est payé (`payment_status = paid`) ou toujours en attente.

### Automatisation des rappels

Un job planifié (`node-cron`) envoie automatiquement un e-mail 7 jours avant la fin de chaque mois à tous les locataires disposant d'une adresse e-mail. Pour l'activer, renseignez :

```
EMAILJS_SERVICE_ID=...
EMAILJS_REMINDER_TEMPLATE_ID=...
EMAILJS_PUBLIC_KEY=...
EMAILJS_PRIVATE_KEY=...
APP_URL=https://app.locatus.com
EMAIL_LOGO_URL=https://app.locatus.com/logo.png
# (Optionnel) Forcer l activation/desactivation manuelle du cron
REMINDER_ENABLED=true
```

Sans ces variables (ou si `REMINDER_ENABLED=false`), le scheduler reste désactivé.

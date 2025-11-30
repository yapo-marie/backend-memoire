import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';
import request from 'supertest';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const require = createRequire(import.meta.url);
const serverPath = path.join(__dirname, '..', 'index.js');

const setupEnv = () => {
  process.env.NODE_ENV = 'test';
  process.env.FIREBASE_DATABASE_URL = 'https://fake-db.example';
  process.env.FIREBASE_DATABASE_SECRET = '';
  process.env.STRIPE_SECRET_KEY = 'sk_test_dummy';
  process.env.CLIENT_ORIGIN = 'http://localhost:3000';
  process.env.SMTP_HOST = 'smtp.test';
  process.env.SMTP_PORT = '587';
  process.env.SMTP_USER = 'user@test';
  process.env.SMTP_PASSWORD = 'pass';
  process.env.MAIL_FROM = 'Test <test@example.com>';
  process.env.MAIL_REPLY_TO = 'reply@example.com';
  process.env.APP_URL = 'http://localhost:3000';
  process.env.EMAIL_LOGO_URL = 'http://localhost:3000/logo.png';
};

const buildResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: String(status),
  text: async () => {
    if (body === undefined || status === 204) {
      return '';
    }
    return typeof body === 'string' ? body : JSON.stringify(body);
  },
});

let firebaseStore;
let mailRequests;
const setupFirebaseMock = () => {
  firebaseStore = {
    locataires: {},
    proprietes: {},
    messages: {},
    remindersLogs: {},
  };
  mailRequests = [];
  let counter = 1;
  global.fetch = vi.fn(async (url, options = {}) => {
    const method = (options.method || 'GET').toUpperCase();
    const match = /\/(locataires|proprietes|messages|remindersLogs)(?:\/([^/.]+))?\.json/.exec(url);
    if (!match) {
      return buildResponse({ error: 'not-found' }, 404);
    }
    const [, resource, id] = match;
    if (method === 'GET') {
      if (id) {
        return buildResponse(firebaseStore[resource][id] ?? null, 200);
      }
      return buildResponse(firebaseStore[resource], 200);
    }
    if (method === 'POST') {
      const payload = options.body ? JSON.parse(options.body) : {};
      const newId = `mock-${counter++}`;
      firebaseStore[resource][newId] = payload;
      return buildResponse({ name: newId }, 200);
    }
    if (method === 'PATCH') {
      if (!id || !firebaseStore[resource][id]) {
        return buildResponse({ error: 'not-found' }, 404);
      }
      const patch = options.body ? JSON.parse(options.body) : {};
      firebaseStore[resource][id] = { ...firebaseStore[resource][id], ...patch };
      return buildResponse(firebaseStore[resource][id], 200);
    }
    if (method === 'DELETE') {
      if (id) {
        delete firebaseStore[resource][id];
      } else {
        firebaseStore[resource] = {};
      }
      return buildResponse(null, 204);
    }
    return buildResponse({ error: 'method-not-allowed' }, 405);
  });
};

const createFakeStripe = () => {
  const sessionsCreate = vi.fn(async () => ({
    id: 'cs_test_123',
    url: 'https://stripe.test/checkout/cs_test_123',
  }));
  const sessionsList = vi.fn(async () => ({ data: [] }));
  return {
    checkout: {
      sessions: {
        create: sessionsCreate,
        list: sessionsList,
      },
    },
    webhooks: {
      constructEvent: vi.fn(),
    },
  };
};

const loadServer = () => {
  delete require.cache[serverPath];
  return require(serverPath);
};

let app;
let fakeStripe;
let fakeMailer;

beforeEach(() => {
  vi.resetModules();
  setupEnv();
  setupFirebaseMock();
  fakeStripe = createFakeStripe();
  global.__TEST_STRIPE_CLIENT__ = fakeStripe;
  fakeMailer = {
    sendMail: vi.fn(async (message) => {
      mailRequests.push(message);
    }),
  };
  global.__TEST_MAIL_TRANSPORT__ = fakeMailer;
  ({ app } = loadServer());
});

afterEach(() => {
  delete global.fetch;
  delete global.__TEST_STRIPE_CLIENT__;
  delete global.__TEST_MAIL_TRANSPORT__;
});

describe('Auth API', () => {
  const registerPayload = {
    name: 'Owner One',
    email: 'owner@example.com',
    password: 'Password123!',
  };

  it('crée un utilisateur et refuse un doublon', async () => {
    const firstResponse = await request(app).post('/api/auth/register').send(registerPayload).expect(201);
    expect(firstResponse.body.user).toMatchObject({
      name: registerPayload.name,
      email: registerPayload.email.toLowerCase(),
      role: 'user',
    });
    expect(firstResponse.body.token).toEqual(expect.any(String));

    await request(app).post('/api/auth/register').send(registerPayload).expect(409);
  });

  it('permet la connexion avec les bons identifiants', async () => {
    await request(app).post('/api/auth/register').send(registerPayload).expect(201);
    const loginResponse = await request(app)
      .post('/api/auth/login')
      .send({ email: registerPayload.email, password: registerPayload.password })
      .expect(200);
    expect(loginResponse.body.user.email).toBe(registerPayload.email.toLowerCase());
    expect(loginResponse.body.token).toEqual(expect.any(String));
  });

  it('refuse la connexion avec un mot de passe incorrect', async () => {
    await request(app).post('/api/auth/register').send(registerPayload).expect(201);
    await request(app)
      .post('/api/auth/login')
      .send({ email: registerPayload.email, password: 'WrongPass123!' })
      .expect(401);
  });
});

describe('Tenants API', () => {
  it('enregistre un locataire valide et le retrouve via GET', async () => {
    const payload = {
      name: 'Jean Dupont',
      email: 'jean.dupont@example.com',
      phone: '+33123456789',
      status: 'pending',
      propertyId: 'prop-1',
      ownerId: 'owner-1',
      note: 'nouveau locataire',
      entryDate: '2024-05-01',
      paymentMonths: 2,
    };
    const createResponse = await request(app).post('/api/tenants').send(payload).expect(201);
    expect(createResponse.body).toMatchObject({
      name: payload.name,
      email: payload.email,
      propertyId: payload.propertyId,
      paymentMonths: payload.paymentMonths,
    });

    const listResponse = await request(app).get('/api/tenants').expect(200);
    expect(Array.isArray(listResponse.body)).toBe(true);
    expect(listResponse.body.length).toBe(1);
    expect(listResponse.body[0].email).toBe(payload.email.toLowerCase());
  });

  it('rejette un locataire avec email invalide', async () => {
    const badPayload = {
      name: 'John Doe',
      email: 'invalid-email',
      phone: '+33123456789',
      entryDate: '2024-01-01',
      paymentMonths: 1,
      ownerId: 'owner-1',
    };
    const response = await request(app).post('/api/tenants').send(badPayload).expect(400);
    expect(response.body).toMatchObject({ message: expect.stringContaining('Email du locataire invalide') });
  });
});

describe('Payments API', () => {
  it('crée une session Stripe Checkout quand les données sont valides', async () => {
    const payload = {
      amount: 1200.5,
      tenantName: 'Alice Martin',
      tenantEmail: 'alice.martin@example.com',
      tenantId: 'tenant-123',
      ownerId: 'owner-1',
      propertyId: 'prop-9',
      propertyName: 'Appartement République',
      paymentMonths: 2,
      dueDate: '2024-06-10',
      successUrl: 'https://app.example.com/success',
      cancelUrl: 'https://app.example.com/cancel',
    };
    const response = await request(app).post('/api/payments/checkout').send(payload).expect(200);
    expect(response.body).toEqual({
      sessionId: 'cs_test_123',
      url: 'https://stripe.test/checkout/cs_test_123',
    });
    expect(fakeStripe.checkout.sessions.create).toHaveBeenCalledTimes(1);
    const callArgs = fakeStripe.checkout.sessions.create.mock.calls[0][0];
    expect(callArgs.metadata).toMatchObject({
      tenantId: payload.tenantId,
      ownerId: payload.ownerId,
      paymentMonths: String(payload.paymentMonths),
    });
  });

  it('retourne 400 si le montant dépasse la limite autorisée', async () => {
    const overlyLargeAmount = 700_000_000;
    const response = await request(app)
      .post('/api/payments/checkout')
      .send({
        amount: overlyLargeAmount,
        tenantName: 'Alice Martin',
        tenantEmail: 'alice.martin@example.com',
        tenantId: 'tenant-123',
        ownerId: 'owner-1',
        propertyId: 'prop-9',
        paymentMonths: 1,
      })
      .expect(400);
    expect(response.body).toMatchObject({
      message: expect.stringContaining('inferieur ou egal a 655 959 993'),
    });
    expect(fakeStripe.checkout.sessions.create).not.toHaveBeenCalled();
  });
});

describe('Reminders API', () => {
  it('retourne les rappels à venir basés sur les locataires et loyers', async () => {
    firebaseStore.locataires = {
      'tenant-1': {
        name: 'Luc Besson',
        email: 'luc@example.com',
        phone: '+33111111111',
        status: 'active',
        propertyId: 'prop-1',
        ownerId: 'owner-1',
        entryDate: '2099-01-01',
        paymentMonths: 2,
      },
    };
    firebaseStore.proprietes = {
      'prop-1': {
        name: 'Appartement Canal',
        rent: 900,
        charges: 100,
        status: 'occupied',
        ownerId: 'owner-1',
      },
    };
    const response = await request(app).get('/api/reminders/upcoming').expect(200);
    expect(response.body.totalRecipients).toBe(1);
    expect(response.body.reminders[0]).toMatchObject({
      tenantName: 'Luc Besson',
      propertyName: 'Appartement Canal',
      amountFormatted: expect.stringContaining('CFA'),
      paymentMonths: 2,
    });
    expect(response.body.reminders[0].dueDate).toMatch(/^2099-03-01/);
  });

  it('permet d envoyer une relance manuelle', async () => {
    firebaseStore.locataires = {
      'tenant-1': {
        name: 'Luc Besson',
        email: 'luc@example.com',
        phone: '+33111111111',
        status: 'active',
        propertyId: 'prop-1',
        ownerId: 'owner-1',
        entryDate: '2024-01-01',
        paymentMonths: 1,
      },
    };
    firebaseStore.proprietes = {
      'prop-1': {
        name: 'Appartement Canal',
        rent: 950,
        charges: 100,
        status: 'occupied',
        ownerId: 'owner-1',
      },
    };
    const response = await request(app)
      .post('/api/reminders/send')
      .send({
        ownerId: 'owner-1',
        tenantIds: ['tenant-1'],
        message: 'Bonjour {{locataire}}, merci de régler {{montant}} avant le {{date}}.',
      })
      .expect(200);
    expect(response.body.sent).toBe(1);
    expect(mailRequests).toHaveLength(1);
    expect(mailRequests[0].to).toBe('luc@example.com');
    const logEntries = Object.values(firebaseStore.remindersLogs);
    expect(logEntries.length).toBe(1);
    expect(logEntries[0].sent).toBe(1);

    const historyResponse = await request(app).get('/api/reminders/history?ownerId=owner-1&limit=10').expect(200);
    expect(Array.isArray(historyResponse.body)).toBe(true);
    expect(historyResponse.body[0].sent).toBe(1);
  });
});

'use strict';

// ─── Config ───────────────────────────────────────────────
const API_BASE = ['localhost', '127.0.0.1'].includes(window.location.hostname)
  ? 'http://localhost:8000'
  : 'https://maheenghouri-customer-success-fte.hf.space';

// ─── DOM refs ─────────────────────────────────────────────
const form         = document.getElementById('support-form');
const submitBtn    = document.getElementById('submit-btn');
const submitText   = document.getElementById('submit-text');
const formCard     = document.getElementById('form-card');
const successCard  = document.getElementById('success-card');
const successId    = document.getElementById('success-ticket-id');
const successEta   = document.getElementById('success-eta');
const resetBtn     = document.getElementById('reset-btn');
const errorBanner  = document.getElementById('error-banner');
const errorText    = document.getElementById('error-banner-text');
const messageEl    = document.getElementById('message');
const charCounter  = document.getElementById('char-counter');

const fields = ['name', 'email', 'subject', 'category', 'message'];

// ─── Character counter ────────────────────────────────────
messageEl.addEventListener('input', () => {
  const n = messageEl.value.length;
  charCounter.textContent = `${n} / 1000`;
  charCounter.classList.toggle('warn', n > 900);
});

// ─── Clear error on typing ────────────────────────────────
fields.forEach((f) => {
  const el = document.getElementById(f);
  if (!el) return;
  el.addEventListener('input', () => clearFieldError(f));
  el.addEventListener('change', () => clearFieldError(f));
});

function clearFieldError(name) {
  const input = document.getElementById(name);
  const err   = document.getElementById(`err-${name}`);
  if (input) input.classList.remove('error');
  if (err) { err.classList.add('hidden'); err.textContent = ''; }
}

function setFieldError(name, msg) {
  const input = document.getElementById(name);
  const err   = document.getElementById(`err-${name}`);
  if (input) input.classList.add('error');
  if (err) { err.classList.remove('hidden'); err.textContent = msg; }
}

function showBanner(msg) {
  errorText.textContent = msg;
  errorBanner.classList.remove('hidden');
}
function hideBanner() { errorBanner.classList.add('hidden'); }

// ─── Validate ─────────────────────────────────────────────
function validate(data) {
  const errors = {};
  if (!data.name || data.name.trim().length < 2) {
    errors.name = 'Please enter your name (at least 2 characters)';
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(data.email || '')) {
    errors.email = 'Please enter a valid email address';
  }
  if (!data.subject || data.subject.trim().length < 5) {
    errors.subject = 'Please enter a subject (at least 5 characters)';
  }
  if (!data.message || data.message.trim().length < 10) {
    errors.message = 'Please describe your issue in more detail';
  }
  return errors;
}

// ─── Submit ───────────────────────────────────────────────
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideBanner();
  fields.forEach(clearFieldError);

  const data = {
    name:     document.getElementById('name').value.trim(),
    email:    document.getElementById('email').value.trim(),
    subject:  document.getElementById('subject').value.trim(),
    category: document.getElementById('category').value,
    priority: document.getElementById('priority').value,
    message:  document.getElementById('message').value.trim(),
  };

  const errors = validate(data);
  const errKeys = Object.keys(errors);
  if (errKeys.length) {
    errKeys.forEach((k) => setFieldError(k, errors[k]));
    const first = document.getElementById(errKeys[0]);
    if (first) first.focus();
    return;
  }

  // Submitting state
  submitBtn.disabled = true;
  submitText.innerHTML = '<span class="spinner"></span> Submitting...';

  try {
    const res = await fetch(`${API_BASE}/support/submit`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(data),
    });

    if (!res.ok) {
      let detail = 'Something went wrong. Please try again.';
      try {
        const errData = await res.json();
        if (errData && errData.detail) detail = typeof errData.detail === 'string' ? errData.detail : JSON.stringify(errData.detail);
      } catch (_) {}
      throw new Error(detail);
    }

    const result = await res.json();
    showSuccess(result);
  } catch (err) {
    showBanner(err.message || 'Network error. Please check your connection.');
    submitBtn.disabled = false;
    submitText.textContent = 'Submit Support Request';
  }
});

// ─── Success ──────────────────────────────────────────────
function showSuccess(result) {
  successId.textContent = result.ticket_id || '—';
  if (result.estimated_response_time) {
    successEta.textContent = result.estimated_response_time;
  }
  formCard.classList.add('hidden');
  successCard.classList.remove('hidden');

  // Navigate to ticket page after a short delay so user sees the ticket id
  setTimeout(() => {
    if (result.ticket_id) {
      window.location.href = `ticket.html?id=${encodeURIComponent(result.ticket_id)}`;
    }
  }, 2500);
}

// ─── Reset ────────────────────────────────────────────────
resetBtn.addEventListener('click', () => {
  form.reset();
  charCounter.textContent = '0 / 1000';
  charCounter.classList.remove('warn');
  submitBtn.disabled = false;
  submitText.textContent = 'Submit Support Request';
  fields.forEach(clearFieldError);
  hideBanner();
  successCard.classList.add('hidden');
  formCard.classList.remove('hidden');
});

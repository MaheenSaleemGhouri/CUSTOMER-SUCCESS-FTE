/**
 * SupportForm.jsx — TechCorp Customer Support Form
 * Exercise 2.2 | Standalone embeddable React component
 *
 * Embed anywhere:
 *   import SupportForm from './SupportForm';
 *   <SupportForm apiEndpoint="/api/support/submit" />
 *
 * States: idle → submitting → success | error
 * Styling: Tailwind CSS (requires Tailwind in host app)
 * Validation: client-side before API call
 */

import { useState, useCallback } from 'react';

// ─── Constants ────────────────────────────────────────────────

const CATEGORIES = [
  { value: 'general',    label: 'General Question' },
  { value: 'technical',  label: 'Technical Support' },
  { value: 'billing',    label: 'Billing Inquiry' },
  { value: 'bug_report', label: 'Bug Report' },
  { value: 'feedback',   label: 'Feedback' },
];

const PRIORITIES = [
  { value: 'low',    label: 'Low - Not urgent' },
  { value: 'medium', label: 'Medium - Need help soon' },
  { value: 'high',   label: 'High - Urgent issue' },
];

const MESSAGE_MAX_CHARS = 1000;

const INITIAL_FORM = {
  name:     '',
  email:    '',
  subject:  '',
  category: 'general',
  priority: 'medium',
  message:  '',
};

// ─── Validation ───────────────────────────────────────────────

function validate(fields) {
  const errors = {};

  if (fields.name.trim().length < 2) {
    errors.name = 'Please enter your name (at least 2 characters)';
  }

  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(fields.email.trim())) {
    errors.email = 'Please enter a valid email address';
  }

  if (fields.subject.trim().length < 5) {
    errors.subject = 'Please enter a subject (at least 5 characters)';
  }

  if (fields.message.trim().length < 10) {
    errors.message = 'Please describe your issue in more detail';
  }

  if (fields.message.length > MESSAGE_MAX_CHARS) {
    errors.message = `Message must be under ${MESSAGE_MAX_CHARS} characters`;
  }

  return errors;
}

// ─── Sub-components ───────────────────────────────────────────

function Spinner() {
  return (
    <svg
      className="animate-spin h-5 w-5 text-white"
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <circle
        className="opacity-25"
        cx="12" cy="12" r="10"
        stroke="currentColor" strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

function FieldError({ message }) {
  if (!message) return null;
  return (
    <p className="mt-1 text-sm text-red-600" role="alert">
      {message}
    </p>
  );
}

function FormField({ label, htmlFor, required, error, children }) {
  return (
    <div>
      <label
        htmlFor={htmlFor}
        className="block text-sm font-medium text-gray-700 mb-1"
      >
        {label}
        {required && <span className="text-red-500 ml-1" aria-hidden="true">*</span>}
      </label>
      {children}
      <FieldError message={error} />
    </div>
  );
}

// ─── Success Screen ───────────────────────────────────────────

function SuccessScreen({ ticketId, estimatedResponseTime, onReset }) {
  return (
    <div className="text-center py-10 px-6">
      {/* Green checkmark icon */}
      <div className="mx-auto flex items-center justify-center h-16 w-16 rounded-full bg-green-100 mb-6">
        <svg
          className="h-8 w-8 text-green-600"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
          aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
      </div>

      <h2 className="text-2xl font-bold text-gray-900 mb-2">
        Thank You!
      </h2>
      <p className="text-gray-600 mb-6 max-w-sm mx-auto">
        Your support request has been submitted successfully.
      </p>

      {/* Ticket ID box */}
      <div className="inline-block bg-gray-100 rounded-lg px-6 py-4 mb-4">
        <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">Your Ticket ID</p>
        <p className="font-mono font-bold text-gray-900 text-xl tracking-widest">
          {ticketId}
        </p>
      </div>

      {/* Estimated response time */}
      {estimatedResponseTime && (
        <p className="text-sm text-gray-500 mb-8">
          Estimated response time: <span className="font-medium text-gray-700">{estimatedResponseTime}</span>
        </p>
      )}

      <button
        type="button"
        onClick={onReset}
        className="inline-flex items-center px-5 py-2.5 border border-gray-300 rounded-lg
                   text-sm font-medium text-gray-700 bg-white hover:bg-gray-50
                   focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500
                   transition-colors duration-150"
      >
        Submit Another Request
      </button>
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────

/**
 * SupportForm — standalone embeddable TechCorp support form.
 *
 * @param {string} apiEndpoint - API URL for form submission (default: '/api/support/submit')
 */
export default function SupportForm({
  apiEndpoint = '/api/support/submit',
  onSuccess = null,
}) {
  // ── State ──────────────────────────────────────────────────
  const [formData, setFormData]   = useState(INITIAL_FORM);
  const [status, setStatus]       = useState('idle');   // 'idle' | 'submitting' | 'success' | 'error'
  const [ticketId, setTicketId]   = useState(null);
  const [error, setError]         = useState(null);
  const [fieldErrors, setFieldErrors] = useState({});
  const [estimatedResponseTime, setEstimatedResponseTime] = useState(null);

  // ── Handlers ───────────────────────────────────────────────

  const handleChange = useCallback((e) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
    // Clear field error on change
    if (fieldErrors[name]) {
      setFieldErrors(prev => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    }
  }, [fieldErrors]);

  const handleReset = useCallback(() => {
    setStatus('idle');
    setFormData(INITIAL_FORM);
    setTicketId(null);
    setError(null);
    setFieldErrors({});
    setEstimatedResponseTime(null);
  }, []);

  const handleSubmit = useCallback(async (e) => {
    e.preventDefault();
    setError(null);

    // Client-side validation
    const errors = validate(formData);
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      const firstErrorId = Object.keys(errors)[0];
      document.getElementById(firstErrorId)?.focus();
      return;
    }

    setStatus('submitting');

    try {
      const res = await fetch(apiEndpoint, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name:     formData.name.trim(),
          email:    formData.email.trim().toLowerCase(),
          subject:  formData.subject.trim(),
          category: formData.category,
          priority: formData.priority,
          message:  formData.message.trim(),
        }),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || 'Submission failed. Please try again.');
      }

      const data = await res.json();
      setTicketId(data.ticket_id);
      setEstimatedResponseTime(data.estimated_response_time || null);
      setStatus('success');
      if (onSuccess) onSuccess(data.ticket_id);

    } catch (err) {
      setStatus('error');
      setError(err.message || 'Something went wrong. Please try again.');
    }
  }, [formData, apiEndpoint]);

  // ── Render: Success ────────────────────────────────────────

  if (status === 'success') {
    return (
      <div className="bg-white rounded-lg shadow-md p-6 max-w-2xl mx-auto">
        <SuccessScreen
          ticketId={ticketId}
          estimatedResponseTime={estimatedResponseTime}
          onReset={handleReset}
        />
      </div>
    );
  }

  // ── Render: Form (idle / submitting / error) ───────────────

  const isSubmitting = status === 'submitting';

  const inputBase = `
    w-full rounded-lg border px-3.5 py-2.5 text-gray-900 text-sm
    placeholder:text-gray-400 shadow-sm
    focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500
    disabled:bg-gray-50 disabled:text-gray-400 disabled:cursor-not-allowed
    transition-colors duration-150
  `;
  const inputNormal = `${inputBase} border-gray-300`;
  const inputErr    = `${inputBase} border-red-400 focus:ring-red-400 focus:border-red-400`;

  return (
    <div className="bg-white rounded-lg shadow-md p-6 max-w-2xl mx-auto">

      {/* Header */}
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900">Contact Support</h1>
        <p className="mt-1 text-sm text-gray-500">
          Fill out the form below and we'll get back to you shortly.
          Fields marked <span className="text-red-500">*</span> are required.
        </p>
      </div>

      <form onSubmit={handleSubmit} noValidate className="space-y-5">

        {/* API-level error banner */}
        {status === 'error' && error && (
          <div
            role="alert"
            className="rounded-lg bg-red-50 border border-red-200 px-4 py-3"
          >
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        {/* Row: Name + Email */}
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">

          <FormField label="Full Name" htmlFor="name" required error={fieldErrors.name}>
            <input
              id="name"
              name="name"
              type="text"
              autoComplete="name"
              placeholder="Jane Smith"
              value={formData.name}
              onChange={handleChange}
              disabled={isSubmitting}
              aria-invalid={!!fieldErrors.name}
              className={fieldErrors.name ? inputErr : inputNormal}
            />
          </FormField>

          <FormField label="Email Address" htmlFor="email" required error={fieldErrors.email}>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              placeholder="jane@yourcompany.com"
              value={formData.email}
              onChange={handleChange}
              disabled={isSubmitting}
              aria-invalid={!!fieldErrors.email}
              className={fieldErrors.email ? inputErr : inputNormal}
            />
          </FormField>

        </div>

        {/* Subject */}
        <FormField label="Subject" htmlFor="subject" required error={fieldErrors.subject}>
          <input
            id="subject"
            name="subject"
            type="text"
            placeholder="Brief description of your issue"
            value={formData.subject}
            onChange={handleChange}
            disabled={isSubmitting}
            aria-invalid={!!fieldErrors.subject}
            className={fieldErrors.subject ? inputErr : inputNormal}
          />
        </FormField>

        {/* Row: Category + Priority */}
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">

          <FormField label="Category" htmlFor="category" required error={fieldErrors.category}>
            <select
              id="category"
              name="category"
              value={formData.category}
              onChange={handleChange}
              disabled={isSubmitting}
              className={`${fieldErrors.category ? inputErr : inputNormal} bg-white`}
            >
              {CATEGORIES.map(({ value, label }) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </FormField>

          <FormField label="Priority" htmlFor="priority" required error={fieldErrors.priority}>
            <select
              id="priority"
              name="priority"
              value={formData.priority}
              onChange={handleChange}
              disabled={isSubmitting}
              className={`${fieldErrors.priority ? inputErr : inputNormal} bg-white`}
            >
              {PRIORITIES.map(({ value, label }) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </FormField>

        </div>

        {/* Message + character counter */}
        <FormField label="Message" htmlFor="message" required error={fieldErrors.message}>
          <textarea
            id="message"
            name="message"
            rows={6}
            placeholder="Please describe your issue in detail…"
            value={formData.message}
            onChange={handleChange}
            disabled={isSubmitting}
            aria-invalid={!!fieldErrors.message}
            aria-describedby="message-char-count"
            className={`${fieldErrors.message ? inputErr : inputNormal} resize-y min-h-[120px]`}
          />
          {/* Character counter */}
          <p
            id="message-char-count"
            className={`mt-1 text-xs tabular-nums ${
              formData.message.length > MESSAGE_MAX_CHARS * 0.9
                ? 'text-red-500 font-medium'
                : 'text-gray-400'
            }`}
            aria-live="polite"
          >
            {formData.message.length}/{MESSAGE_MAX_CHARS} characters
          </p>
        </FormField>

        {/* Submit button */}
        <button
          type="submit"
          disabled={isSubmitting}
          style={{ backgroundColor: isSubmitting ? undefined : '#2563EB' }}
          className="
            w-full inline-flex items-center justify-center gap-2
            px-5 py-3 rounded-lg text-sm font-semibold text-white
            hover:bg-blue-700 active:bg-blue-800
            focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500
            disabled:bg-gray-400 disabled:cursor-not-allowed
            transition-colors duration-150 shadow-sm
          "
          aria-busy={isSubmitting}
        >
          {isSubmitting ? (
            <>
              <Spinner />
              <span>Submitting...</span>
            </>
          ) : (
            <span>Submit Support Request</span>
          )}
        </button>

      </form>
    </div>
  );
}

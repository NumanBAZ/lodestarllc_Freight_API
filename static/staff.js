"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const staffState = {
  authState: "checking",
  username: "",
  csrfToken: "",
  bookingEnabled: false,
  selectedQuote: null,
  lastPayload: null,
  bookingPending: false
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showError(element, message) {
  element.textContent = message;
  element.hidden = !message;
}

function errorMessage(result, fallback) {
  if (result?.booking_status === "pending_owner_confirmation") {
    return "Booking is awaiting account owner approval and has not been placed yet.";
  }
  const detail = result?.detail ?? result?.data?.error ?? result?.data?.message;
  if (Array.isArray(detail)) return detail.join(" ");
  return String(detail || fallback);
}

function formatPrice(value) {
  const number = Number(String(value ?? "").replaceAll("$", "").replaceAll(",", ""));
  if (!Number.isFinite(number)) return "Rate unavailable";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(number);
}

function transitText(value) {
  if (value === undefined || value === null || value === "") return "Not provided";
  const number = Number(value);
  return Number.isFinite(number) ? `${number} business ${number === 1 ? "day" : "days"}` : String(value);
}

function futureDate() {
  const value = new Date();
  value.setDate(value.getDate() + 1);
  return value.toISOString().slice(0, 10);
}

function renderAuthState(authState, session = {}) {
  const authenticated = authState === "authenticated";
  const checking = authState === "checking";
  staffState.authState = authState;
  staffState.username = authenticated ? String(session.username || "") : "";
  staffState.csrfToken = authenticated ? String(session.csrfToken || "") : "";
  staffState.bookingEnabled = authenticated && session.bookingEnabled === true;
  if (!authenticated) staffState.selectedQuote = null;

  $("#authLoading").hidden = !checking;
  $("#loginView").hidden = checking || authenticated;
  $("#panelView").hidden = !authenticated;
  $("#staffIdentity").hidden = !authenticated;
  $("#staffUsername").textContent = staffState.username;
}

async function restoreSession() {
  renderAuthState("checking");
  try {
    const response = await fetch("/api/staff/session", {
      headers: { Accept: "application/json" },
      cache: "no-store"
    });
    if (!response.ok) {
      renderAuthState("anonymous");
      return;
    }
    const result = await response.json();
    renderAuthState("authenticated", {
      username: result.username,
      csrfToken: result.csrf_token,
      bookingEnabled: result.booking_enabled
    });
  } catch {
    renderAuthState("anonymous");
  }
}

function checkedValues(name) {
  return $$(`input[name="${name}"]:checked`).map((input) => input.value);
}

function buildQuotePayload() {
  return {
    origin_zip: $("#originZip").value.trim(),
    destination_zip: $("#destinationZip").value.trim(),
    pickup_date: $("#pickupDate").value,
    pallets: Number($("#pallets").value),
    total_weight_lbs: Number($("#totalWeight").value),
    length_in: Number($("#length").value),
    width_in: Number($("#width").value),
    height_in: Number($("#height").value),
    freight_class: $("#freightClass").value.trim(),
    commodity: $("#commodity").value.trim(),
    pickup_services: checkedValues("pickupService"),
    delivery_services: checkedValues("deliveryService")
  };
}

function shipmentSummary(payload) {
  return `${payload.pallets} pallet${payload.pallets === 1 ? "" : "s"}, ${payload.total_weight_lbs} lb total, ${payload.length_in} × ${payload.width_in} × ${payload.height_in} in, class ${payload.freight_class}`;
}

function quoteCard(option, index) {
  return `<article class="staff-quote-card">
    <div><p class="eyebrow">${option.bookable ? "BOOKABLE" : "RATE ONLY"}</p><h3>${escapeHtml(option.carrier_name || "Carrier not provided")}</h3></div>
    <div class="quote-price">${escapeHtml(formatPrice(option.price_usd))}</div>
    <dl class="quote-facts">
      <div><dt>Transit</dt><dd>${escapeHtml(transitText(option.transit_days))}</dd></div>
      <div><dt>Service Level</dt><dd>${escapeHtml(option.service_level || "Not provided")}</dd></div>
      <div><dt>Quote ID</dt><dd>${escapeHtml(option.quote_id || "Not provided")}</dd></div>
      <div><dt>Option ID</dt><dd>${escapeHtml(option.option_id || "Not provided")}</dd></div>
      <div><dt>Bookable</dt><dd>${option.bookable ? "Yes" : "No"}</dd></div>
    </dl>
    ${option.bookable
      ? `<button class="button button-primary" type="button" data-book-quote="${index}">Book Shipment</button>`
      : '<button class="button button-quiet" type="button" disabled>Not Bookable</button>'}
  </article>`;
}

function renderQuotes(options, mode) {
  const results = $("#staffResults");
  results.hidden = false;
  if (!options.length) {
    results.innerHTML = '<div class="panel booking-success"><h2>No rates returned</h2><p>Review the shipment and try another date.</p></div>';
    return;
  }
  results.innerHTML = `<div class="results-heading"><div><p class="eyebrow">${escapeHtml(mode.toUpperCase())} RESULTS</p><h2>Available Quotes</h2></div><span>${options.length} result${options.length === 1 ? "" : "s"}</span></div>
    <div class="quote-grid">${options.map(quoteCard).join("")}</div>`;
  $$('[data-book-quote]', results).forEach((button) => button.addEventListener("click", () => {
    openBookingDialog(options[Number(button.dataset.bookQuote)]);
  }));
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

function selectedQuoteMarkup(quote) {
  return `
    <div><span>Carrier</span><strong>${escapeHtml(quote.carrier_name)}</strong></div>
    <div><span>Price</span><strong>${escapeHtml(formatPrice(quote.price_usd))}</strong></div>
    <div><span>Transit</span><strong>${escapeHtml(transitText(quote.transit_days))}</strong></div>
    <div><span>Service Level</span><strong>${escapeHtml(quote.service_level || "Not provided")}</strong></div>
    <div><span>Quote ID</span><strong>${escapeHtml(quote.quote_id)}</strong></div>
    <div><span>Option ID</span><strong>${escapeHtml(quote.option_id || "Not provided")}</strong></div>
    <div><span>Pickup → Delivery</span><strong>${escapeHtml(`${staffState.lastPayload.origin_zip} → ${staffState.lastPayload.destination_zip}`)}</strong></div>
    <div><span>Shipment</span><strong>${escapeHtml(shipmentSummary(staffState.lastPayload))}</strong></div>`;
}

function openBookingDialog(quote) {
  if (!quote.bookable || !quote.quote_token) return;
  staffState.selectedQuote = quote;
  $("#selectedQuoteSummary").innerHTML = selectedQuoteMarkup(quote);
  $("#bookingDisabledNotice").hidden = staffState.bookingEnabled;
  $("#confirmBooking").disabled = !staffState.bookingEnabled;
  $("#confirmBooking").textContent = staffState.bookingEnabled ? "Confirm Booking" : "Booking Disabled";
  $('[data-stop="pickup"] [name="zipCode"]').value = staffState.lastPayload.origin_zip;
  $('[data-stop="delivery"] [name="zipCode"]').value = staffState.lastPayload.destination_zip;
  showError($("#bookingError"), "");
  $("#bookingDialog").showModal();
}

function stopPayload(name) {
  const fieldset = $(`[data-stop="${name}"]`);
  return Object.fromEntries(
    ["company", "street", "street2", "city", "state", "zipCode", "contactName", "phone", "email"]
      .map((field) => [field, $(`[name="${field}"]`, fieldset).value.trim()])
      .filter(([, value]) => value)
  );
}

function windowPayload(name) {
  const fieldset = $(`[data-stop="${name}"]`);
  const from = $('[name="windowFrom"]', fieldset).value;
  const to = $('[name="windowTo"]', fieldset).value;
  return from || to ? { from, to } : undefined;
}

$("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  showError($("#loginError"), "");
  const button = $('button[type="submit"]', form);
  button.disabled = true;
  renderAuthState("checking");
  try {
    const response = await fetch("/api/staff/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("#staffUser").value.trim(), password: $("#staffPassword").value })
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(result, "Unable to sign in."));
    form.reset();
    renderAuthState("authenticated", {
      username: result.username,
      csrfToken: result.csrf_token,
      bookingEnabled: result.booking_enabled
    });
  } catch (error) {
    renderAuthState("anonymous");
    showError($("#loginError"), error.message);
  } finally {
    button.disabled = false;
  }
});

$("#logoutButton").addEventListener("click", async () => {
  const currentSession = {
    username: staffState.username,
    csrfToken: staffState.csrfToken,
    bookingEnabled: staffState.bookingEnabled
  };
  renderAuthState("checking");
  try {
    const response = await fetch("/api/staff/logout", {
      method: "POST",
      headers: { "X-Staff-CSRF": currentSession.csrfToken }
    });
    if (!response.ok) throw new Error("Unable to log out. Please try again.");
    $("#staffResults").hidden = true;
    $("#staffResults").innerHTML = "";
    renderAuthState("anonymous");
  } catch (error) {
    renderAuthState("authenticated", currentSession);
    showError($("#quoteError"), error.message);
  }
});

$("#staffQuoteForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  showError($("#quoteError"), "");
  const mode = $('input[name="mode"]:checked')?.value;
  if (!mode) return;
  const button = $("#quoteButton");
  button.disabled = true;
  button.textContent = "Requesting Rates…";
  staffState.lastPayload = buildQuotePayload();
  try {
    const response = await fetch(`/api/staff/quote/${mode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Staff-CSRF": staffState.csrfToken },
      body: JSON.stringify(staffState.lastPayload)
    });
    const result = await response.json().catch(() => ({}));
    if (response.status === 401) {
      renderAuthState("anonymous");
      showError($("#loginError"), "Your staff session expired. Sign in again.");
      return;
    }
    if (!response.ok || result.ok === false) throw new Error(errorMessage(result, "Unable to retrieve staff quotes."));
    renderQuotes(result.options || [], mode);
  } catch (error) {
    showError($("#quoteError"), error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Get Staff Quote";
  }
});

$("#bookingForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!staffState.bookingEnabled) {
    showError($("#bookingError"), "Production booking is currently disabled.");
    return;
  }
  if (staffState.bookingPending || !event.currentTarget.reportValidity()) return;
  const pickupWindow = windowPayload("pickup");
  const deliveryWindow = windowPayload("delivery");
  if ((pickupWindow && (!pickupWindow.from || !pickupWindow.to)) || (deliveryWindow && (!deliveryWindow.from || !deliveryWindow.to))) {
    showError($("#bookingError"), "Enter both From and To values for each time window.");
    return;
  }
  staffState.bookingPending = true;
  showError($("#bookingError"), "");
  const button = $("#confirmBooking");
  button.disabled = true;
  button.textContent = "Confirming Booking…";
  const payload = {
    quote_token: staffState.selectedQuote.quote_token,
    pickup: stopPayload("pickup"),
    delivery: stopPayload("delivery"),
    pickup_window: pickupWindow,
    delivery_window: deliveryWindow,
    reference: $("#bookingReference").value.trim(),
    notes: $("#bookingNotes").value.trim()
  };
  try {
    const response = await fetch("/api/staff/book", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Staff-CSRF": staffState.csrfToken },
      body: JSON.stringify(payload)
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.ok === false) throw new Error(errorMessage(result, "Booking could not be completed."));
    $("#bookingDialog").close();
    $("#staffResults").innerHTML = `<div class="booking-success">
      <p class="eyebrow">BOOKING CONFIRMED</p><h2>Shipment booked successfully</h2>
      <dl class="quote-facts">
        <div><dt>Shipment ID</dt><dd>${escapeHtml(result.shipment_id || "Not provided")}</dd></div>
        <div><dt>Status</dt><dd>${escapeHtml(result.booking_status || "booked")}</dd></div>
        <div><dt>Tracking</dt><dd>${escapeHtml(result.tracking_number || result.tracking_dashboard || "Not yet available")}</dd></div>
        <div><dt>Carrier</dt><dd>${escapeHtml(result.carrier || "Not provided")}</dd></div>
        <div><dt>Booked Price</dt><dd>${escapeHtml(formatPrice(result.booked_price))}</dd></div>
      </dl></div>`;
  } catch (error) {
    showError($("#bookingError"), error.message);
    staffState.bookingPending = false;
    button.disabled = false;
    button.textContent = "Confirm Booking";
  }
});

$("#closeBooking").addEventListener("click", () => {
  if (!staffState.bookingPending) $("#bookingDialog").close();
});

$("#pickupDate").min = new Date().toISOString().slice(0, 10);
$("#pickupDate").value = futureDate();
restoreSession();

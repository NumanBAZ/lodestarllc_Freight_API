"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const staffState = {
  authState: "checking",
  username: "",
  csrfToken: "",
  bookingEnabled: false,
  selectedQuote: null,
  selectedCustomerRequest: null,
  customerRequests: [],
  activeWorkspace: "requests",
  requestFilter: "all",
  requestSearch: "",
  requestPage: 1,
  requestPageSize: 20,
  requestTotal: 0,
  requestTotalPages: 1,
  newRequestCount: 0,
  lastPayload: null,
  bookingPending: false
};

let requestSearchTimer;

const staffLocations = window.LodestarLocationResolver.attach([
  { inputId: "originLocation", zipId: "originZip", cityId: "originCity", stateId: "originState", optionsId: "originLocationOptions", errorId: "originLocationError", label: "Origin" },
  { inputId: "destinationLocation", zipId: "destinationZip", cityId: "destinationCity", stateId: "destinationState", optionsId: "destinationLocationOptions", errorId: "destinationLocationError", label: "Destination" }
]);

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

function localDateValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function futureDate() {
  const value = new Date();
  value.setDate(value.getDate() + 1);
  return localDateValue(value);
}

function renderAuthState(authState, session = {}) {
  const authenticated = authState === "authenticated";
  const checking = authState === "checking";
  staffState.authState = authState;
  staffState.username = authenticated ? String(session.username || "") : "";
  staffState.csrfToken = authenticated ? String(session.csrfToken || "") : "";
  staffState.bookingEnabled = authenticated && session.bookingEnabled === true;
  if (!authenticated) {
    staffState.selectedQuote = null;
    staffState.selectedCustomerRequest = null;
    staffState.customerRequests = [];
    staffState.requestPage = 1;
    staffState.requestTotal = 0;
    staffState.requestTotalPages = 1;
    staffState.newRequestCount = 0;
  }

  $("#authLoading").hidden = !checking;
  $("#loginView").hidden = checking || authenticated;
  $("#panelView").hidden = !authenticated;
  $("#staffIdentity").hidden = !authenticated;
  $("#staffUsername").textContent = staffState.username;
  if (authenticated) renderStaffWorkspace("requests");
}

function renderStaffWorkspace(workspace) {
  const activeWorkspace = workspace === "quote" ? "quote" : "requests";
  staffState.activeWorkspace = activeWorkspace;
  $("#customerRequestsWorkspace").hidden = activeWorkspace !== "requests";
  $("#staffQuoteWorkspace").hidden = activeWorkspace !== "quote";
  $$('[data-staff-workspace]').forEach((button) => {
    const active = button.dataset.staffWorkspace === activeWorkspace;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
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
    await loadCustomerRequests();
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

function dateTimeText(value) {
  if (!value) return "Not provided";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

function customerEmailHref(email) {
  const address = String(email || "").trim();
  return address ? `mailto:${address}` : "";
}

function customerPhoneHref(phone) {
  const number = String(phone || "").trim().replace(/[^+\d]/g, "");
  return number ? `tel:${number}` : "";
}

function statusClass(status) {
  return ["approved", "rejected", "booked"].includes(status) ? `is-${status}` : "";
}

function freightTypeLabel(value) {
  const freightType = String(value || "").trim();
  return freightType.toLowerCase() === "ftl" ? "53' Dry Van (FTL)" : freightType || "—";
}

function routeLocationLabel(route, prefix) {
  const zip = route?.[prefix] || "";
  const city = route?.[`${prefix}_city`] || "";
  const state = route?.[`${prefix}_state`] || "";
  return city && state ? `${city}, ${state}${zip ? ` (${zip})` : ""}` : zip || "—";
}

function locationDetailItem(label, city, state, zip) {
  const cityState = city && state ? `${city}, ${state}` : "";
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(cityState || zip || "Not provided")}${cityState && zip ? `<small>${escapeHtml(zip)}</small>` : ""}</strong></div>`;
}

function requestRow(request) {
  return `<button class="customer-request-row" type="button" data-customer-request="${escapeHtml(request.id)}">
    <span><strong>${escapeHtml(request.customer || "Not provided")}</strong></span>
    <span>${escapeHtml(`${routeLocationLabel(request.route, "origin")} → ${routeLocationLabel(request.route, "destination")}`)}</span>
    <span>${escapeHtml(request.carrier || "Not provided")}</span>
    <span>${escapeHtml(formatPrice(request.price_usd))}</span>
    <span>${escapeHtml(freightTypeLabel(request.freight_type))}</span>
    <span>${escapeHtml(dateTimeText(request.created_at))}</span>
    <span class="request-status ${statusClass(request.status)}">${escapeHtml(request.status || "new")}</span>
  </button>`;
}

function renderCustomerRequests() {
  const message = $("#customerRequestsMessage");
  const list = $("#customerRequestsList");
  const pagination = $("#requestPagination");
  const requests = staffState.customerRequests;
  const count = $("#newRequestCount");
  count.textContent = String(staffState.newRequestCount);
  count.hidden = staffState.newRequestCount === 0;
  $("#requestPageStatus").textContent = `Page ${staffState.requestPage} of ${staffState.requestTotalPages}`;
  $("#previousRequestPage").disabled = staffState.requestPage <= 1;
  $("#nextRequestPage").disabled = staffState.requestPage >= staffState.requestTotalPages;
  pagination.hidden = staffState.requestTotal === 0;
  if (!requests.length) {
    list.hidden = true;
    list.innerHTML = "";
    message.hidden = false;
    message.textContent = staffState.requestSearch
      ? "No customer quote requests match your search."
      : staffState.requestFilter === "all"
        ? "No customer quote requests yet."
        : `No ${staffState.requestFilter} customer quote requests.`;
    return;
  }
  list.innerHTML = requests.map(requestRow).join("");
  list.hidden = false;
  message.hidden = true;
  $$('[data-customer-request]', list).forEach((button) => button.addEventListener("click", () => {
    openCustomerRequestDetail(button.dataset.customerRequest);
  }));
}

async function loadCustomerRequests() {
  if (staffState.authState !== "authenticated") return;
  const message = $("#customerRequestsMessage");
  const list = $("#customerRequestsList");
  message.hidden = false;
  message.textContent = "Loading customer quote requests…";
  list.hidden = true;
  try {
    const query = new URLSearchParams({
      page: String(staffState.requestPage),
      page_size: String(staffState.requestPageSize),
      status: staffState.requestFilter
    });
    if (staffState.requestSearch) query.set("search", staffState.requestSearch);
    const response = await fetch(`/api/staff/quote-requests?${query}`, {
      headers: { "X-Staff-CSRF": staffState.csrfToken },
      cache: "no-store"
    });
    const result = await response.json().catch(() => ({}));
    if (response.status === 401) {
      renderAuthState("anonymous");
      showError($("#loginError"), "Your staff session expired. Sign in again.");
      return;
    }
    if (!response.ok) throw new Error(errorMessage(result, "Unable to load customer quote requests."));
    staffState.customerRequests = Array.isArray(result.requests) ? result.requests : [];
    staffState.requestPage = Number(result.page) || 1;
    staffState.requestTotal = Number(result.total) || 0;
    staffState.requestTotalPages = Number(result.total_pages) || 1;
    staffState.newRequestCount = Number(result.new_count) || 0;
    renderCustomerRequests();
  } catch (error) {
    message.textContent = error.message;
  }
}

function detailItem(label, value) {
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "Not provided")}</strong></div>`;
}

function customerContactActions(customer) {
  const emailHref = customerEmailHref(customer.email);
  const phoneHref = customerPhoneHref(customer.phone);
  return `<div class="contact-customer-block"><strong>Contact Customer</strong>
    ${emailHref || phoneHref ? `<div class="customer-contact-actions">
      ${emailHref ? `<a class="button button-primary" href="${escapeHtml(emailHref)}">Send Email</a>` : ""}
      ${phoneHref ? `<a class="button button-quiet" href="${escapeHtml(phoneHref)}">Call Customer</a>` : ""}
    </div>` : "<small>No contact methods available.</small>"}
  </div>`;
}

function requestDetailMarkup(request) {
  const customer = request.customer || {};
  const shipment = request.shipment || {};
  const quote = request.selected_quote || {};
  const accessorials = shipment.accessorials || {};
  return `
    <section class="request-detail-section"><h3>Customer</h3><div class="request-detail-grid">
      ${detailItem("Full Name", customer.full_name)}${detailItem("Email", customer.email)}${detailItem("Phone", customer.phone)}
      ${detailItem("Status", request.status)}${detailItem("Created At", dateTimeText(request.created_at))}${detailItem("Shipment ID", request.shipment_id)}
      ${request.reject_reason ? detailItem("Reject Reason", request.reject_reason) : ""}
    </div>${customerContactActions(customer)}</section>
    <section class="request-detail-section"><h3>Shipment</h3><div class="request-detail-grid">
      ${locationDetailItem("Origin", shipment.origin_city, shipment.origin_state, shipment.origin_zip)}${locationDetailItem("Destination", shipment.destination_city, shipment.destination_state, shipment.destination_zip)}${detailItem("Pickup Date", shipment.pickup_date)}${detailItem("Freight Type", freightTypeLabel(request.freight_type))}
      ${detailItem("Pallets", shipment.pallets)}${detailItem("Total Weight", shipment.total_weight_lbs ? `${shipment.total_weight_lbs} lb` : null)}${detailItem("Dimensions", `${shipment.length_in || "—"} × ${shipment.width_in || "—"} × ${shipment.height_in || "—"} in`)}
      ${detailItem("Freight Class", shipment.freight_class)}${detailItem("Commodity", shipment.commodity || "Optional / not provided")}${detailItem("Pickup Services", (accessorials.pickup || []).join(", ") || "None")}
      ${detailItem("Delivery Services", (accessorials.delivery || []).join(", ") || "None")}
    </div></section>
    <section class="request-detail-section"><h3>Selected Quote</h3><div class="request-detail-grid">
      ${detailItem("Carrier", quote.carrier_name)}${detailItem("Price", formatPrice(quote.price_usd))}${detailItem("Service Level", quote.service_level)}
      ${detailItem("Transit", transitText(quote.transit_days))}${detailItem("Quote ID", quote.quote_id)}${detailItem("Option ID", quote.option_id)}
      ${detailItem("Quote Expiration", dateTimeText(quote.expires_at))}${detailItem("Bookable", quote.bookable ? "Yes" : "No")}
    </div></section>`;
}

function renderCustomerRequestDetail(request) {
  staffState.selectedCustomerRequest = request;
  $("#customerRequestDetail").innerHTML = requestDetailMarkup(request);
  $("#approveCustomerRequest").hidden = !["new", "rejected"].includes(request.status);
  $("#rejectCustomerRequest").hidden = request.status !== "new";
  $("#bookCustomerRequest").hidden = !request.booking_quote_token || request.status !== "approved";
  showError($("#requestActionError"), "");
}

async function openCustomerRequestDetail(requestId) {
  try {
    const response = await fetch(`/api/staff/quote-requests/${encodeURIComponent(requestId)}`, {
      headers: { "X-Staff-CSRF": staffState.csrfToken },
      cache: "no-store"
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(errorMessage(result, "Unable to load quote request."));
    renderCustomerRequestDetail(result);
    $("#requestDetailDialog").showModal();
  } catch (error) {
    $("#customerRequestsMessage").hidden = false;
    $("#customerRequestsMessage").textContent = error.message;
  }
}

async function updateCustomerRequestStatus(status, rejectReason = "", errorTarget = $("#requestActionError")) {
  const request = staffState.selectedCustomerRequest;
  if (!request?.id) return false;
  showError(errorTarget, "");
  try {
    const response = await fetch(`/api/staff/quote-requests/${encodeURIComponent(request.id)}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "X-Staff-CSRF": staffState.csrfToken },
      body: JSON.stringify({ status, reject_reason: rejectReason })
    });
    const result = await response.json().catch(() => ({}));
    if (response.status === 401) {
      renderAuthState("anonymous");
      showError($("#loginError"), "Your staff session expired. Sign in again.");
      return false;
    }
    if (!response.ok) throw new Error(errorMessage(result, "Unable to update this quote request."));
    renderCustomerRequestDetail(result);
    await loadCustomerRequests();
    return true;
  } catch (error) {
    showError(errorTarget, error.message);
    return false;
  }
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
  $("#bookingForm").reset();
  $("#selectedQuoteSummary").innerHTML = selectedQuoteMarkup(quote);
  $("#bookingDisabledNotice").hidden = staffState.bookingEnabled;
  $("#confirmBooking").disabled = !staffState.bookingEnabled;
  $("#confirmBooking").textContent = staffState.bookingEnabled ? "Confirm Booking" : "Booking Disabled";
  $('[data-stop="pickup"] [name="zipCode"]').value = staffState.lastPayload.origin_zip;
  $('[data-stop="delivery"] [name="zipCode"]').value = staffState.lastPayload.destination_zip;
  if (quote.customer) {
    $('[data-stop="pickup"] [name="contactName"]').value = quote.customer.full_name || "";
    $('[data-stop="pickup"] [name="email"]').value = quote.customer.email || "";
    $('[data-stop="pickup"] [name="phone"]').value = quote.customer.phone || "";
  }
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
    await loadCustomerRequests();
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
    $("#customerBookingResult").hidden = true;
    $("#customerBookingResult").innerHTML = "";
    $("#customerRequestsList").hidden = true;
    $("#customerRequestsList").innerHTML = "";
    staffState.customerRequests = [];
    renderAuthState("anonymous");
  } catch (error) {
    renderAuthState("authenticated", currentSession);
    showError($("#quoteError"), error.message);
  }
});

$("#staffQuoteForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!await staffLocations.resolveAll()) return;
  if (!form.reportValidity()) return;
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
    const bookingResult = result.customer_request_id ? $("#customerBookingResult") : $("#staffResults");
    bookingResult.innerHTML = `<div class="booking-success">
      <p class="eyebrow">BOOKING CONFIRMED</p><h2>Shipment booked successfully</h2>
      <dl class="quote-facts">
        <div><dt>Shipment ID</dt><dd>${escapeHtml(result.shipment_id || "Not provided")}</dd></div>
        <div><dt>Status</dt><dd>${escapeHtml(result.booking_status || "booked")}</dd></div>
        <div><dt>Tracking</dt><dd>${escapeHtml(result.tracking_number || result.tracking_dashboard || "Not yet available")}</dd></div>
        <div><dt>Carrier</dt><dd>${escapeHtml(result.carrier || "Not provided")}</dd></div>
        <div><dt>Booked Price</dt><dd>${escapeHtml(formatPrice(result.booked_price))}</dd></div>
      </dl></div>`;
    bookingResult.hidden = false;
    if (result.customer_request_id) {
      $("#requestDetailDialog").close();
      await loadCustomerRequests();
    }
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

$("#refreshCustomerRequests").addEventListener("click", loadCustomerRequests);
$$('[data-staff-workspace]').forEach((button) => button.addEventListener("click", () => {
  renderStaffWorkspace(button.dataset.staffWorkspace);
}));
$$('[data-request-filter]').forEach((button) => button.addEventListener("click", () => {
  staffState.requestFilter = button.dataset.requestFilter;
  staffState.requestPage = 1;
  $$('[data-request-filter]').forEach((filterButton) => {
    const active = filterButton === button;
    filterButton.classList.toggle("is-active", active);
    filterButton.setAttribute("aria-pressed", String(active));
  });
  loadCustomerRequests();
}));
$("#requestSearch").addEventListener("input", (event) => {
  window.clearTimeout(requestSearchTimer);
  requestSearchTimer = window.setTimeout(() => {
    staffState.requestSearch = event.target.value.trim();
    staffState.requestPage = 1;
    loadCustomerRequests();
  }, 250);
});
$("#previousRequestPage").addEventListener("click", () => {
  if (staffState.requestPage <= 1) return;
  staffState.requestPage -= 1;
  loadCustomerRequests();
});
$("#nextRequestPage").addEventListener("click", () => {
  if (staffState.requestPage >= staffState.requestTotalPages) return;
  staffState.requestPage += 1;
  loadCustomerRequests();
});
$("#closeRequestDetail").addEventListener("click", () => $("#requestDetailDialog").close());
$("#approveCustomerRequest").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  await updateCustomerRequestStatus("approved");
  button.disabled = false;
});
$("#rejectCustomerRequest").addEventListener("click", () => {
  $("#rejectRequestForm").reset();
  showError($("#rejectRequestError"), "");
  $("#rejectRequestDialog").showModal();
});
$("#closeRejectRequest").addEventListener("click", () => $("#rejectRequestDialog").close());
$("#rejectRequestForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#confirmRejectRequest");
  button.disabled = true;
  const updated = await updateCustomerRequestStatus("rejected", $("#rejectReason").value.trim(), $("#rejectRequestError"));
  button.disabled = false;
  if (updated) $("#rejectRequestDialog").close();
});
$("#bookCustomerRequest").addEventListener("click", () => {
  const request = staffState.selectedCustomerRequest;
  if (!request?.booking_quote_token) return;
  const shipment = request.shipment || {};
  const selectedQuote = request.selected_quote || {};
  staffState.lastPayload = shipment;
  $("#requestDetailDialog").close();
  openBookingDialog({
    ...selectedQuote,
    quote_token: request.booking_quote_token,
    customer: request.customer
  });
});

$("#pickupDate").min = localDateValue(new Date());
$("#pickupDate").value = futureDate();
restoreSession();

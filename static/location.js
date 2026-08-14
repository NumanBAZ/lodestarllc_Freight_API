"use strict";

(() => {
  const ZIP_PATTERN = /^\d{5}$/;
  const CITY_STATE_PATTERN = /^[A-Za-z][A-Za-z .'-]{1,80},\s*[A-Za-z]{2}$/;

  class LocationField {
    constructor(config) {
      this.input = document.getElementById(config.inputId);
      this.zipInput = document.getElementById(config.zipId);
      this.cityInput = document.getElementById(config.cityId);
      this.stateInput = document.getElementById(config.stateId);
      this.options = document.getElementById(config.optionsId);
      this.error = document.getElementById(config.errorId);
      this.label = config.label;
      this.timer = null;
      this.controller = null;

      this.input.addEventListener("input", () => this.handleInput());
      this.input.addEventListener("blur", () => {
        window.setTimeout(() => this.hideOptions(), 160);
      });
      this.input.addEventListener("keydown", (event) => {
        if (event.key === "Escape") this.hideOptions();
      });
    }

    clearSelection() {
      this.zipInput.value = "";
      this.cityInput.value = "";
      this.stateInput.value = "";
      delete this.input.dataset.resolvedDisplay;
    }

    setError(message = "") {
      const field = this.input.closest(".location-field");
      field?.classList.toggle("has-error", Boolean(message));
      this.input.classList.toggle("is-invalid", Boolean(message));
      this.input.setAttribute("aria-invalid", message ? "true" : "false");
      this.error.textContent = message;
      this.error.hidden = !message;
    }

    handleInput() {
      this.clearSelection();
      this.setError();
      this.hideOptions();
      window.clearTimeout(this.timer);
      const query = this.input.value.trim();
      if (!ZIP_PATTERN.test(query) && !CITY_STATE_PATTERN.test(query)) return;
      this.timer = window.setTimeout(() => this.lookup(false), 320);
    }

    hideOptions() {
      this.options.hidden = true;
      this.input.setAttribute("aria-expanded", "false");
    }

    renderOptions(options) {
      this.options.replaceChildren();
      for (const option of options) {
        const button = document.createElement("button");
        button.type = "button";
        button.setAttribute("role", "option");
        button.textContent = `${option.city}, ${option.state} — ${option.zip}`;
        button.addEventListener("click", () => this.select(option));
        this.options.appendChild(button);
      }
      this.options.hidden = false;
      this.input.setAttribute("aria-expanded", "true");
    }

    select(option) {
      const display = `${option.city}, ${option.state} — ${option.zip}`;
      this.input.value = display;
      this.input.dataset.resolvedDisplay = display;
      this.zipInput.value = option.zip;
      this.cityInput.value = option.city;
      this.stateInput.value = option.state;
      this.setError();
      this.hideOptions();
    }

    async lookup(showErrors) {
      const query = this.input.value.trim();
      const isZip = ZIP_PATTERN.test(query);
      if (!isZip && !CITY_STATE_PATTERN.test(query)) {
        if (showErrors) this.setError(`Enter a valid ${this.label} ZIP or City, State.`);
        return false;
      }

      this.controller?.abort();
      this.controller = new AbortController();
      try {
        const response = await fetch(`/api/locations/resolve?query=${encodeURIComponent(query)}`, {
          headers: { Accept: "application/json" },
          signal: this.controller.signal
        });
        const result = await response.json().catch(() => ({}));
        const options = Array.isArray(result.options) ? result.options : [];
        if (!response.ok || !options.length) {
          this.hideOptions();
          if (showErrors) this.setError(`No valid US location was found for ${this.label.toLowerCase()}.`);
          return false;
        }
        if (isZip) {
          this.select(options[0]);
          return true;
        }
        this.renderOptions(options);
        if (showErrors) this.setError(`Select a ZIP option for ${this.label.toLowerCase()}.`);
        return false;
      } catch (error) {
        if (error.name === "AbortError") return false;
        this.hideOptions();
        if (showErrors) this.setError("Location lookup is temporarily unavailable. Please try again.");
        return false;
      }
    }

    async ensureSelected() {
      if (this.zipInput.value && this.input.dataset.resolvedDisplay === this.input.value) {
        this.setError();
        return true;
      }
      return this.lookup(true);
    }
  }

  window.LodestarLocationResolver = {
    attach(configs) {
      const fields = configs.map((config) => new LocationField(config));
      return {
        async resolveAll() {
          const results = await Promise.all(fields.map((field) => field.ensureSelected()));
          return results.every(Boolean);
        }
      };
    }
  };
})();

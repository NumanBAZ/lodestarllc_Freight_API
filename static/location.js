"use strict";

(() => {
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
      this.activeIndex = -1;

      this.input.addEventListener("input", () => this.handleInput());
      this.input.addEventListener("focus", () => this.handleFocus());
      this.input.addEventListener("blur", () => {
        window.setTimeout(() => this.hideOptions(), 180);
      });
      this.input.addEventListener("keydown", (event) => this.handleKeydown(event));
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

    canSearch(query) {
      return query.length >= 2 && /^[A-Za-z0-9][A-Za-z0-9 .,'-]*$/.test(query);
    }

    handleFocus() {
      if (this.zipInput.value) return;
      const query = this.input.value.trim();
      if (this.canSearch(query)) this.lookup(false);
      else this.renderStatus("Start typing a city, state, or ZIP.", "is-hint");
    }

    handleInput() {
      this.clearSelection();
      this.setError();
      window.clearTimeout(this.timer);
      this.controller?.abort();
      const query = this.input.value.trim();
      if (!this.canSearch(query)) {
        this.renderStatus(
          query ? "Type at least 2 letters or ZIP digits." : "Start typing a city, state, or ZIP.",
          "is-hint"
        );
        return;
      }
      this.renderStatus("Searching locations…", "is-loading");
      this.timer = window.setTimeout(() => this.lookup(false), 300);
    }

    handleKeydown(event) {
      if (event.key === "Escape") {
        this.hideOptions();
        return;
      }
      const buttons = [...this.options.querySelectorAll('button[role="option"]')];
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (this.options.hidden) {
          if (this.canSearch(this.input.value.trim())) this.lookup(false);
          return;
        }
        if (!buttons.length) return;
        const change = event.key === "ArrowDown" ? 1 : -1;
        const next = this.activeIndex < 0
          ? (change > 0 ? 0 : buttons.length - 1)
          : (this.activeIndex + change + buttons.length) % buttons.length;
        this.setActiveOption(next, buttons);
        return;
      }
      if (event.key === "Enter" && !this.options.hidden && this.activeIndex >= 0) {
        event.preventDefault();
        buttons[this.activeIndex]?.click();
      }
    }

    setActiveOption(index, buttons = [...this.options.querySelectorAll('button[role="option"]')]) {
      this.activeIndex = index;
      buttons.forEach((button, buttonIndex) => {
        const active = buttonIndex === index;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
      });
      const active = buttons[index];
      if (active) {
        this.input.setAttribute("aria-activedescendant", active.id);
        active.scrollIntoView({ block: "nearest" });
      }
    }

    showOptions() {
      this.options.hidden = false;
      this.input.setAttribute("aria-expanded", "true");
    }

    hideOptions() {
      this.options.hidden = true;
      this.activeIndex = -1;
      this.input.setAttribute("aria-expanded", "false");
      this.input.removeAttribute("aria-activedescendant");
    }

    renderStatus(message, className) {
      this.options.replaceChildren();
      const status = document.createElement("div");
      status.className = `location-status ${className}`;
      status.setAttribute("role", "status");
      status.textContent = message;
      this.options.appendChild(status);
      this.activeIndex = -1;
      this.showOptions();
    }

    renderOptions(options) {
      this.options.replaceChildren();
      options.forEach((option, index) => {
        const button = document.createElement("button");
        button.id = `${this.options.id}-option-${index}`;
        button.type = "button";
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected", "false");
        button.textContent = `${option.city}, ${option.state} — ${option.zip}`;
        button.addEventListener("pointerdown", (event) => event.preventDefault());
        button.addEventListener("click", () => this.select(option));
        this.options.appendChild(button);
      });
      this.activeIndex = -1;
      this.showOptions();
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
      if (!this.canSearch(query)) {
        this.renderStatus("Type at least 2 letters or ZIP digits.", "is-hint");
        if (showErrors) this.setError(`Enter a valid ${this.label} ZIP or city.`);
        return false;
      }

      this.controller?.abort();
      this.controller = new AbortController();
      this.renderStatus("Searching locations…", "is-loading");
      try {
        const response = await fetch(`/api/locations/resolve?query=${encodeURIComponent(query)}`, {
          headers: { Accept: "application/json" },
          signal: this.controller.signal
        });
        const result = await response.json().catch(() => ({}));
        const options = Array.isArray(result.options) ? result.options : [];
        if (!response.ok || !options.length) {
          this.renderStatus("No matching US locations found.", "is-empty");
          if (showErrors) this.setError(`Select a valid location for ${this.label.toLowerCase()}.`);
          return false;
        }
        this.renderOptions(options);
        if (showErrors) this.setError(`Select a ZIP option for ${this.label.toLowerCase()}.`);
        return false;
      } catch (error) {
        if (error.name === "AbortError") return false;
        this.renderStatus("Location search is temporarily unavailable.", "is-empty");
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

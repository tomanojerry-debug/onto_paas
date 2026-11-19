/* @cloudfour/elastic-textarea 1.0.7
 * https://github.com/cloudfour/elastic-textarea/tree/1.0.7
 * MIT; see elastic-textarea.LICENSE.txt
 * Local adaptation: reset rows before sizing, so a CSS max-height never traps a large rows value.
 */
customElements.define(
  "elastic-textarea",
  class extends HTMLElement {
    connectedCallback() {
      /**
       * Find all inner textareas and use their rows attributes as a minimum count
       */
      [...this.querySelectorAll("textarea")].forEach((textareaEl) => {
        textareaEl.dataset.minRows = textareaEl.rows || 2;

        // If textareas are prefilled they may need to be resized
        this.update(textareaEl);
      });

      // Use event delegation to listen for textarea inputs and update the areas
      this.addEventListener("input", ({ target }) => {
        if (!(target instanceof HTMLTextAreaElement)) return;

        this.update(target);
      });
    }

    /**
     * Determine if the element is overflowing
     */
    isScrolling(textareaEl) {
      return textareaEl.scrollHeight > textareaEl.clientHeight;
    }

    /** Grow until the textarea stops scrolling */
    grow(textareaEl) {
      // Store initial height of textarea
      let previousHeight = textareaEl.clientHeight;
      let rows = this.rows(textareaEl);

      while (this.isScrolling(textareaEl)) {
        rows++;
        textareaEl.rows = rows;

        // Get height after rows change is made
        const newHeight = textareaEl.clientHeight;

        // If the height hasn't changed, break the loop
        // This is an important safety check in case the height is hard coded
        if (newHeight === previousHeight) break;

        // Store the updated height for the next comparison and proceed
        previousHeight = newHeight;
      }
    }

    update(textareaEl) {
      const scrollTop = textareaEl.scrollTop;
      textareaEl.rows = parseInt(textareaEl.dataset.minRows, 10);
      this.grow(textareaEl);
      textareaEl.scrollTop = scrollTop;
    }

    rows(textareaEl) {
      return textareaEl.rows || parseInt(textareaEl.dataset.minRows);
    }
  }
);

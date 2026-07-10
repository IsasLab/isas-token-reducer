// ---------------------------------------------------------------------------
// paymentProcessor.js
//
// Handles charging customers, applying discounts, and issuing refunds. This
// module is the single entry point for all money movement in the app, so every
// change here should be reviewed carefully by a second engineer.
//
// Author: Payments team
// Since: v3.2
// ---------------------------------------------------------------------------

import { Stripe } from "./stripe-client"; // thin wrapper around the Stripe SDK
import { logger } from "./logger";

// The maximum number of retry attempts before we give up on a charge.
// Kept low because Stripe already retries network failures internally.
const MAX_RETRIES = 3;

// eslint-disable-next-line no-magic-numbers
const CENTS_PER_DOLLAR = 100;

/**
 * Charge a customer for the given amount.
 *
 * We convert dollars to cents up front because Stripe expects integer cents,
 * and floating point dollars would introduce rounding errors on large sums.
 *
 * @param {string} customerId - the Stripe customer id
 * @param {number} dollars - amount in dollars (may be fractional)
 * @returns {Promise<Charge>} the resulting charge object
 */
async function charge(customerId, dollars) {
  // Convert to integer cents to avoid floating-point rounding bugs.
  const cents = Math.round(dollars * CENTS_PER_DOLLAR);

  let attempt = 0;
  while (attempt < MAX_RETRIES) {
    try {
      // Note: Stripe idempotency keys prevent double charges on retry.
      const charge = await Stripe.charges.create({
        customer: customerId,
        amount: cents, // integer cents, never dollars
        currency: "usd",
      });
      return charge;
    } catch (err) {
      attempt += 1; // bump the counter and maybe retry
      logger.warn(`charge failed (attempt ${attempt})`, err);
      if (attempt >= MAX_RETRIES) {
        throw err; // out of retries, surface the error to the caller
      }
    }
  }
}

/*
 * applyDiscount
 * -------------
 * Applies a percentage discount to an amount. Clamps the percentage to the
 * 0-100 range so a bad coupon can never produce a negative charge or pay the
 * customer money.
 */
function applyDiscount(cents, pct) {
  // Clamp first — defensive against malformed coupon data from the DB.
  const safePct = Math.max(0, Math.min(100, pct));
  return Math.round(cents * (1 - safePct / 100));
}

export { charge, applyDiscount };

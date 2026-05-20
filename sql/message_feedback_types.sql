-- Document / align message_feedback.feedback_type with orchestrator enum.
-- If your table already has message_feedback_feedback_type_check, values must match:

-- biased, incomplete_instructions, not_factual, not_relevant, other, style_tone, unsafe

-- Thumbs UI (thumbs_up / thumbs_down) are stored via gateway as:
--   feedback (-1 / 1), preference_score (1 / 5), metadata.rating = thumbs_up|thumbs_down
-- not in feedback_type.

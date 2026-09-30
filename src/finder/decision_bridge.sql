CREATE OR REPLACE FUNCTION finder_sync_legacy_feedback() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d finder_decisions%ROWTYPE;
BEGIN
  IF TG_OP='DELETE' THEN
    SELECT * INTO d FROM finder_decisions WHERE watch_id=OLD.watch_id AND marketplace=OLD.marketplace AND marketplace_item_id=OLD.marketplace_item_id;
    IF FOUND THEN
      UPDATE finder_decisions SET verdict=NULL,updated_at=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
        WHERE watch_id=OLD.watch_id AND marketplace=OLD.marketplace AND marketplace_item_id=OLD.marketplace_item_id AND verdict IS NOT NULL;
      -- Legacy clear means clear identity; purchasing is a separate retained outcome.
      -- Keep the suppression mirror for workers that predate the new dashboard.
      IF d.purchased AND EXISTS(SELECT 1 FROM finder_inbox WHERE watch_id=OLD.watch_id AND marketplace=OLD.marketplace AND marketplace_item_id=OLD.marketplace_item_id) THEN
        INSERT INTO finder_verdicts VALUES(OLD.watch_id,OLD.marketplace,OLD.marketplace_item_id,'bought',COALESCE(d.tier,'unknown'),COALESCE(d.decided_at,d.updated_at)) ON CONFLICT DO NOTHING;
      END IF;
    END IF;
    RETURN OLD;
  END IF;
  SELECT * INTO d FROM finder_decisions WHERE watch_id=NEW.watch_id AND marketplace=NEW.marketplace AND marketplace_item_id=NEW.marketplace_item_id;
  IF FOUND AND ((NEW.verdict='bought' AND d.purchased) OR (NEW.verdict IN ('mine','other','unsure') AND d.verdict=NEW.verdict)) THEN RETURN NEW; END IF;
  INSERT INTO finder_decisions(watch_id,marketplace,marketplace_item_id,verdict,purchased,tier,decided_at,updated_at,prediction,legacy)
  VALUES(NEW.watch_id,NEW.marketplace,NEW.marketplace_item_id,
    CASE WHEN NEW.verdict IN ('mine','other','unsure') THEN NEW.verdict END,NEW.verdict='bought',
    CASE WHEN NEW.verdict IN ('mine','other','unsure') THEN NEW.tier END,
    CASE WHEN NEW.verdict IN ('mine','other','unsure') THEN NEW.decided_at END,NEW.decided_at,
    CASE WHEN NEW.verdict IN ('mine','other','unsure') THEN json_build_object('status',NEW.tier,'source','legacy_tier_only') END,row_to_json(NEW))
  ON CONFLICT(watch_id,marketplace,marketplace_item_id) DO UPDATE SET
    verdict=CASE WHEN NEW.verdict='bought' THEN finder_decisions.verdict ELSE EXCLUDED.verdict END,
    purchased=finder_decisions.purchased OR EXCLUDED.purchased,
    tier=COALESCE(finder_decisions.tier,EXCLUDED.tier),decided_at=COALESCE(finder_decisions.decided_at,EXCLUDED.decided_at),
    prediction=COALESCE(finder_decisions.prediction,EXCLUDED.prediction),legacy=COALESCE(finder_decisions.legacy,EXCLUDED.legacy),updated_at=EXCLUDED.updated_at;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS finder_feedback_compatibility ON finder_verdicts;
CREATE TRIGGER finder_feedback_compatibility AFTER INSERT OR UPDATE OR DELETE ON finder_verdicts FOR EACH ROW EXECUTE FUNCTION finder_sync_legacy_feedback();

-- Handmatig een hoofdstuk kiezen: zolang er een focus staat, komen nieuwe kaarten alleen
-- daaruit. Herhalingen blijven ongemoeid -- die volgen de planning, niet de volgorde.

-- Het hoofdstuk van een thema staat al in zijn naam: alles voor de ' · ' ("A1.5 · Les couleurs"
-- -> "A1.5", "Werkwoorden · Transport en de weg" -> "Werkwoorden"). Een thema zonder dat teken
-- (de kwiziq-lijsten) valt terug op zijn niveau. Afgeleid in plaats van los bijgehouden, omdat
-- thema's hier met de hand in SQL worden geladen: een kolom die je moet invullen, vergeet je.
ALTER TABLE french.themes ADD COLUMN chapter text
    GENERATED ALWAYS AS (
        CASE WHEN name LIKE '% · %' THEN split_part(name, ' · ', 1) ELSE level END
    ) STORED;

-- Per gebruiker, want de een kan met het boek meelopen terwijl de ander nog inhaalt.
-- NULL betekent: geen focus, gewoon de volgorde van themes.position.
ALTER TABLE french.users ADD COLUMN focus text;

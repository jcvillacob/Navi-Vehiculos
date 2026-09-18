"""Categoria de reglas 'postratamiento' (DEF/urea, DPF, SCR, derates).

Tercera categoria de regla Geotab, con el mismo modelo que 'habito_seguro':
alcance global a la database (sin motor ni banda de RPM) y clasificacion
explicita en `description` contra su propio enum cerrado.

Los CHECK de categoria se RECREAN, no se agregan: el bootstrap runtime los crea
con ADD CONSTRAINT protegido por nombre, asi que un CHECK ya existente nunca se
actualiza solo. Los dos CHECK historicos de `description` se reemplazan por uno
que liga el enum a su categoria.
"""
from __future__ import annotations

from alembic import op


revision = "20260917_0001"
down_revision = "20260914_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables_exist = bind.exec_driver_sql(
        """
        SELECT
            to_regclass('geotab_rules') IS NOT NULL
            AND to_regclass('geotab_rule_applications') IS NOT NULL;
        """
    ).scalar()
    if not tables_exist:
        return

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_geotab_rules_category'
                  AND pg_get_constraintdef(oid) LIKE '%postratamiento%'
            ) THEN
                ALTER TABLE geotab_rules
                DROP CONSTRAINT IF EXISTS ck_geotab_rules_category;
                ALTER TABLE geotab_rules
                ADD CONSTRAINT ck_geotab_rules_category
                CHECK (category IN (
                    'operacion', 'habito_seguro', 'postratamiento'
                ));
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_geotab_rule_applications_category'
                  AND pg_get_constraintdef(oid) LIKE '%postratamiento%'
            ) THEN
                ALTER TABLE geotab_rule_applications
                DROP CONSTRAINT IF EXISTS ck_geotab_rule_applications_category;
                ALTER TABLE geotab_rule_applications
                ADD CONSTRAINT ck_geotab_rule_applications_category
                CHECK (category IN (
                    'operacion', 'habito_seguro', 'postratamiento'
                ));
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_geotab_rule_app_description_by_category'
            ) THEN
                ALTER TABLE geotab_rule_applications
                    DROP CONSTRAINT IF EXISTS ck_geotab_rule_app_description_habito_only,
                    DROP CONSTRAINT IF EXISTS ck_geotab_rule_app_description;
                ALTER TABLE geotab_rule_applications
                ADD CONSTRAINT ck_geotab_rule_app_description_by_category
                CHECK (
                    description IS NULL
                    OR (
                        category = 'habito_seguro'
                        AND description IN (
                            'Excesos de velocidad', 'Giros bruscos',
                            'Excesos de RPM', 'Frenadas bruscas',
                            'Baches o Resaltos fuertes', 'Aceleraciones bruscas'
                        )
                    )
                    OR (
                        category = 'postratamiento'
                        AND description IN (
                            'Nivel bajo de DEF', 'Calidad de DEF',
                            'Regeneracion DPF requerida', 'Regeneracion DPF inhibida',
                            'Nivel alto de hollin DPF', 'Temperatura alta de escape',
                            'Falla SCR o sensor NOx', 'Derate por postratamiento'
                        )
                    )
                );
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_geotab_rule_app_postratamiento_scope'
            ) THEN
                ALTER TABLE geotab_rule_applications
                ADD CONSTRAINT ck_geotab_rule_app_postratamiento_scope
                CHECK (
                    category <> 'postratamiento'
                    OR (motor_id IS NULL AND band IS NULL AND is_descenso = FALSE)
                );
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables_exist = bind.exec_driver_sql(
        """
        SELECT
            to_regclass('geotab_rules') IS NOT NULL
            AND to_regclass('geotab_rule_applications') IS NOT NULL;
        """
    ).scalar()
    if not tables_exist:
        return

    # Los datos de la categoria nueva no sobreviven al downgrade: sin ellos los
    # CHECK originales no se pueden recrear.
    op.execute(
        """
        DELETE FROM geotab_rule_applications WHERE category = 'postratamiento';
        DELETE FROM geotab_rules WHERE category = 'postratamiento';
        """
    )
    op.execute(
        """
        ALTER TABLE geotab_rule_applications
            DROP CONSTRAINT IF EXISTS ck_geotab_rule_app_postratamiento_scope,
            DROP CONSTRAINT IF EXISTS ck_geotab_rule_app_description_by_category;

        ALTER TABLE geotab_rule_applications
        ADD CONSTRAINT ck_geotab_rule_app_description_habito_only
        CHECK (description IS NULL OR category = 'habito_seguro');

        ALTER TABLE geotab_rule_applications
        ADD CONSTRAINT ck_geotab_rule_app_description
        CHECK (description IS NULL OR description IN (
            'Excesos de velocidad', 'Giros bruscos', 'Excesos de RPM',
            'Frenadas bruscas', 'Baches o Resaltos fuertes',
            'Aceleraciones bruscas'
        ));

        ALTER TABLE geotab_rule_applications
        DROP CONSTRAINT IF EXISTS ck_geotab_rule_applications_category;
        ALTER TABLE geotab_rule_applications
        ADD CONSTRAINT ck_geotab_rule_applications_category
        CHECK (category IN ('operacion', 'habito_seguro'));

        ALTER TABLE geotab_rules
        DROP CONSTRAINT IF EXISTS ck_geotab_rules_category;
        ALTER TABLE geotab_rules
        ADD CONSTRAINT ck_geotab_rules_category
        CHECK (category IN ('operacion', 'habito_seguro'));
        """
    )

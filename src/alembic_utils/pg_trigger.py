# pylint: disable=unused-argument,invalid-name,line-too-long
from typing import List, Optional

from parse import parse
from sqlalchemy import text as sql_text

from alembic_utils.exceptions import SQLParseFailure
from alembic_utils.replaceable_entity import ReplaceableEntity


class PGTrigger(ReplaceableEntity):
    """A PostgreSQL Trigger compatible with `alembic revision --autogenerate`

    **Parameters:**

    * **schema** - *str*: A SQL schema name
    * **signature** - *str*: A SQL function's call signature
    * **definition** - *str*:  The remainig function body and identifiers

    Postgres Create Trigger Specification:

        CREATE [ CONSTRAINT ] TRIGGER name { BEFORE | AFTER | INSTEAD OF } { event [ OR ... ] }
        ON table
        [ FROM referenced_table_name ]
        [ NOT DEFERRABLE | [ DEFERRABLE ] { INITIALLY IMMEDIATE | INITIALLY DEFERRED } ]
        [ FOR [ EACH ] { ROW | STATEMENT } ]
        [ WHEN ( condition ) ]
        EXECUTE PROCEDURE function_name ( arguments )

    Limitations:
        - "table" must be qualified with a schema name e.g. public.account vs account
        - trigger schema must match table schema

    """

    _template = "create{:s}trigger{:s}{signature}{:s}{event}{:s}ON{:s}{on_entity}{:s}{action}"

    @classmethod
    def _parse_result(cls, sql: str) -> Optional:
        result = parse(cls._template, sql.strip(), case_sensitive=False)
        return result

    @classmethod
    def from_sql(cls, sql: str) -> "PGTrigger":
        """Create an instance instance from a SQL string"""
        result = parse(cls._template, sql.strip(), case_sensitive=False)
        if result is not None:
            # remove possible quotes from signature
            signature = result["signature"]
            event = result["event"]
            on_entity = result["on_entity"]
            action = result["action"]

            if "." not in on_entity:
                raise SQLParseFailure(
                    f'Failed to parse SQL into PGFunction the table/view {on_entity} must be qualified with a schema e.g. "public.account"'
                )

            schema = on_entity.split(".")[0]

            definition_template = " {event} ON {on_entity} {action}"
            definition = definition_template.format(event=event, on_entity=on_entity, action=action)

            return cls(
                schema=schema,
                signature=signature,
                definition=definition,
            )
        raise SQLParseFailure(f'Failed to parse SQL into PGTrigger """{sql}"""')

    def to_sql_statement_create(self) -> str:
        """ Generates a SQL "create function" statement for PGFunction """

        # We need to parse and replace the schema qualifier on the table for simulate_entity to
        # operate
        _def = self.definition.strip()
        _template = "{event}{:s}ON{:s}{on_entity}{:s}{action}"
        match = parse(_template, _def)
        if not match:
            raise SQLParseFailure(f'Failed to parse SQL into PGTrigger.definition """{_def}"""')

        event = match["event"]
        action = match["action"]

        # Ensure entity is qualified with schema
        on_entity = match["on_entity"]
        if "." in on_entity:
            _, _, on_entity = on_entity.partition(".")
        on_entity = f"{self.schema}.{on_entity}"

        # Re-render the definition ensuring the table is qualified with
        def_rendered = _template.replace("{:s}", " ").format(
            event=event, on_entity=on_entity, action=action
        )

        return sql_text(f"CREATE TRIGGER {self.signature} {def_rendered}")

    @property
    def on_entity(self) -> str:
        """Get the fully qualified name of the table/view the trigger is applied to"""
        create_statement = self.to_sql_statement_create()
        result = parse(self._template, create_statement.strip(), case_sensitive=False)
        return result["on_entity"]

    def to_sql_statement_drop(self) -> str:
        """Generates a SQL "drop function" statement for PGFunction"""
        return sql_text(f"DROP TRIGGER {self.signature} ON {self.schema}.{self.on_entity};")

    def to_sql_statement_create_or_replace(self) -> str:
        """ Generates a SQL "create or replace function" statement for PGFunction """
        return self.to_sql_statement_drop() + sql_text(" ") + self.to_sql_statement_create()

    @classmethod
    def from_database(cls, connection, schema) -> List["PGFunction"]:
        """Get a list of all functions defined in the db"""

        # NOTE(OR): Schema is looked up by unqualified trigger name. Mismatches possible

        sql = sql_text(
            f"""
        select
            tgname trigger_name,
            pg_get_triggerdef(oid) definition,
            itr.trigger_schema as table_schema
        from
            pg_trigger pgt
            inner join information_schema.triggers itr
                    on lower(pgt.tgname) = lower(itr.trigger_name)
        where
            not tgisinternal
            and itr.event_object_schema = :schema
        """
        )

        rows = connection.execute(sql, schema=schema).fetchall()

        db_triggers = [PGTrigger.from_sql(x[1]) for x in rows]

        for trig in db_triggers:
            assert trig is not None

        return db_triggers

    def get_compare_identity_query(self):
        """Only called in simulation. alembic_util schema will only have 1 record"""
        return f"""
        select
            tgname trigger_name,
            itr.trigger_schema as table_schema
        from
            pg_trigger pgt
            inner join information_schema.triggers itr
                on lower(pgt.tgname) = lower(itr.trigger_name)
        where
            not tgisinternal
            and itr.event_object_schema = '{self.schema}'
        """

    def get_compare_definition_query(self):
        """Only called in simulation. alembic_util schema will only have 1 record"""
        return f"""
        select
            pg_get_triggerdef(oid) definition,
        from
            pg_trigger pgt
            inner join information_schema.triggers itr
                on lower(pgt.tgname) = lower(itr.trigger_name)
        where
            not tgisinternal
            and itr.event_object_schema = '{self.schema}'
        """

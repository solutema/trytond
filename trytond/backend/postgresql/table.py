# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
from trytond.transaction import Transaction
from trytond.backend.table import TableHandlerInterface
import logging

__all__ = ['TableHandler']

logger = logging.getLogger(__name__)


class TableHandler(TableHandlerInterface):

    def __init__(self, model, module_name=None, history=False):
        super(TableHandler, self).__init__(model,
                module_name=module_name, history=history)
        self._columns = {}
        self._constraints = []
        self._fk_deltypes = {}
        self._indexes = []

        transaction = Transaction()
        cursor = transaction.connection.cursor()
        # Create sequence if necessary
        if not self.sequence_exist(self.sequence_name):
            cursor.execute('CREATE SEQUENCE "%s"' % self.sequence_name)

        # Create new table if necessary
        if not self.table_exist(self.table_name):
            cursor.execute('CREATE TABLE "%s" ()' % self.table_name)
        self.table_schema = transaction.database.get_table_schema(
            transaction.connection, self.table_name)

        cursor.execute('SELECT tableowner = current_user FROM pg_tables '
            'WHERE tablename = %s AND schemaname = %s',
            (self.table_name, self.table_schema))
        self.is_owner, = cursor.fetchone()

        if model.__doc__ and self.is_owner:
            cursor.execute('COMMENT ON TABLE "%s" IS \'%s\'' %
                (self.table_name, model.__doc__.replace("'", "''")))

        self._update_definitions(columns=True)
        if 'id' not in self._columns:
            if not self.history:
                cursor.execute('ALTER TABLE "%s" '
                    'ADD COLUMN id INTEGER '
                    'DEFAULT nextval(\'"%s"\') NOT NULL'
                    % (self.table_name, self.sequence_name))
                cursor.execute('ALTER TABLE "%s" '
                    'ADD PRIMARY KEY(id)' % self.table_name)
            else:
                cursor.execute('ALTER TABLE "%s" '
                    'ADD COLUMN id INTEGER' % self.table_name)
            self._update_definitions(columns=True)
        if self.history and '__id' not in self._columns:
            cursor.execute('ALTER TABLE "%s" '
                'ADD COLUMN __id INTEGER '
                'DEFAULT nextval(\'"%s"\') NOT NULL' %
                (self.table_name, self.sequence_name))
            cursor.execute('ALTER TABLE "%s" '
                'ADD PRIMARY KEY(__id)' % self.table_name)
        else:
            default = "nextval('%s'::regclass)" % self.sequence_name
            if self.history:
                if self._columns['__id']['default'] != default:
                    cursor.execute('ALTER TABLE "%s" '
                        'ALTER __id SET DEFAULT %s'
                        % (self.table_name, default))
            if self._columns['id']['default'] != default:
                    cursor.execute('ALTER TABLE "%s" '
                        'ALTER id SET DEFAULT %s'
                        % (self.table_name, default))
        self._update_definitions()

    @staticmethod
    def table_exist(table_name):
        transaction = Transaction()
        return bool(transaction.database.get_table_schema(
                transaction.connection, table_name))

    @staticmethod
    def table_rename(old_name, new_name):
        cursor = Transaction().connection.cursor()
        # Rename table
        if (TableHandler.table_exist(old_name)
                and not TableHandler.table_exist(new_name)):
            cursor.execute('ALTER TABLE "%s" RENAME TO "%s"'
                % (old_name, new_name))
        # Rename sequence
        old_sequence = old_name + '_id_seq'
        new_sequence = new_name + '_id_seq'
        TableHandler.sequence_rename(old_sequence, new_sequence)
        # Rename history table
        old_history = old_name + "__history"
        new_history = new_name + "__history"
        if (TableHandler.table_exist(old_history)
                and not TableHandler.table_exist(new_history)):
            cursor.execute('ALTER TABLE "%s" RENAME TO "%s"'
                % (old_history, new_history))

    @classmethod
    def sequence_schema(cls, sequence_name):
        transaction = Transaction()
        cursor = transaction.connection.cursor()
        for schema in transaction.database.search_path:
            cursor.execute('SELECT 1 '
                'FROM information_schema.sequences '
                'WHERE sequence_name = %s AND sequence_schema = %s',
                (sequence_name, schema))
            if cursor.rowcount:
                return schema

    @classmethod
    def sequence_exist(cls, sequence_name):
        return bool(cls.sequence_schema(sequence_name))

    @staticmethod
    def sequence_rename(old_name, new_name):
        cursor = Transaction().connection.cursor()
        if (TableHandler.sequence_exist(old_name)
                and not TableHandler.sequence_exist(new_name)):
            cursor.execute('ALTER TABLE "%s" RENAME TO "%s"'
                % (old_name, new_name))

    def column_exist(self, column_name):
        return column_name in self._columns

    def column_rename(self, old_name, new_name, exception=False):
        cursor = Transaction().connection.cursor()
        if (self.column_exist(old_name)
                and not self.column_exist(new_name)):
            cursor.execute('ALTER TABLE "%s" '
                'RENAME COLUMN "%s" TO "%s"'
                % (self.table_name, old_name, new_name))
            self._update_definitions(columns=True)
        elif exception and self.column_exist(new_name):
            raise Exception('Unable to rename column %s.%s to %s.%s: '
                '%s.%s already exist!'
                % (self.table_name, old_name, self.table_name, new_name,
                    self.table_name, new_name))

    def _update_definitions(self,
            columns=None, constraints=None, indexes=None):
        if columns is None and constraints is None and indexes is None:
            columns = constraints = indexes = True
        cursor = Transaction().connection.cursor()
        if columns:
            self._columns = {}
            # Fetch columns definitions from the table
            cursor.execute('SELECT '
                'column_name, udt_name, is_nullable, '
                'character_maximum_length, '
                'column_default '
                'FROM information_schema.columns '
                'WHERE table_name = %s AND table_schema = %s',
                (self.table_name, self.table_schema))
            for column, typname, nullable, size, default in cursor.fetchall():
                self._columns[column] = {
                    'typname': typname,
                    'notnull': True if nullable == 'NO' else False,
                    'size': size,
                    'default': default,
                    }

        if constraints:
            # fetch constraints for the table
            cursor.execute('SELECT constraint_name '
                'FROM information_schema.table_constraints '
                'WHERE table_name = %s AND table_schema = %s',
                (self.table_name, self.table_schema))
            self._constraints = [c for c, in cursor.fetchall()]

            cursor.execute('SELECT k.column_name, r.delete_rule '
                'FROM information_schema.key_column_usage AS k '
                'JOIN information_schema.referential_constraints AS r '
                'ON r.constraint_schema = k.constraint_schema '
                'AND r.constraint_name = k.constraint_name '
                'WHERE k.table_name = %s AND k.table_schema = %s',
                (self.table_name, self.table_schema))
            self._fk_deltypes = dict(cursor.fetchall())

        if indexes:
            # Fetch indexes defined for the table
            cursor.execute("SELECT cl2.relname "
                "FROM pg_index ind "
                    "JOIN pg_class cl on (cl.oid = ind.indrelid) "
                    "JOIN pg_namespace n ON (cl.relnamespace = n.oid) "
                    "JOIN pg_class cl2 on (cl2.oid = ind.indexrelid) "
                "WHERE cl.relname = %s AND n.nspname = %s",
                (self.table_name, self.table_schema))
            self._indexes = [l[0] for l in cursor.fetchall()]

    @property
    def _field2module(self):
        cursor = Transaction().connection.cursor()
        cursor.execute('SELECT f.name, f.module '
            'FROM ir_model_field f '
                'JOIN ir_model m on (f.model=m.id) '
            'WHERE m.model = %s',
            (self.object_name,))
        return dict(cursor)

    def alter_size(self, column_name, column_type):
        cursor = Transaction().connection.cursor()
        cursor.execute("ALTER TABLE \"%s\" "
            "RENAME COLUMN \"%s\" TO _temp_change_size"
            % (self.table_name, column_name))
        cursor.execute("ALTER TABLE \"%s\" "
            "ADD COLUMN \"%s\" %s"
            % (self.table_name, column_name, column_type))
        cursor.execute("UPDATE \"%s\" "
            "SET \"%s\" = _temp_change_size::%s"
            % (self.table_name, column_name, column_type))
        cursor.execute("ALTER TABLE \"%s\" "
            "DROP COLUMN _temp_change_size"
            % (self.table_name,))
        self._update_definitions(columns=True)

    def alter_type(self, column_name, column_type):
        cursor = Transaction().connection.cursor()
        cursor.execute('ALTER TABLE "' + self.table_name + '" '
            'ALTER "' + column_name + '" TYPE ' + column_type)
        self._update_definitions(columns=True)

    def db_default(self, column_name, value):
        if value in [True, False]:
            test = str(value).lower()
        else:
            test = value
        if self._columns[column_name]['default'] != test:
            cursor = Transaction().connection.cursor()
            cursor.execute('ALTER TABLE "' + self.table_name + '" '
                'ALTER COLUMN "' + column_name + '" SET DEFAULT %s',
                (value,))

    def add_raw_column(self, column_name, column_type, column_format,
            default_fun=None, field_size=None, migrate=True, string=''):
        cursor = Transaction().connection.cursor()

        def comment():
            if self.is_owner:
                cursor.execute('COMMENT ON COLUMN "%s"."%s" IS \'%s\'' %
                    (self.table_name, column_name, string.replace("'", "''")))
        if self.column_exist(column_name):
            if (column_name in ('create_date', 'write_date')
                    and column_type[1].lower() != 'timestamp(6)'):
                # Migrate dates from timestamp(0) to timestamp
                cursor.execute('ALTER TABLE "' + self.table_name + '" '
                    'ALTER COLUMN "' + column_name + '" TYPE timestamp')
            comment()
            if not migrate:
                return
            base_type = column_type[0].lower()
            if base_type != self._columns[column_name]['typname']:
                if (self._columns[column_name]['typname'], base_type) in [
                        ('varchar', 'text'),
                        ('text', 'varchar'),
                        ('date', 'timestamp'),
                        ('int4', 'float8'),
                        ]:
                    self.alter_type(column_name, base_type)
                else:
                    logger.warning(
                        'Unable to migrate column %s on table %s '
                        'from %s to %s.',
                        column_name, self.table_name,
                        self._columns[column_name]['typname'], base_type)

            if (base_type == 'varchar'
                    and self._columns[column_name]['typname'] == 'varchar'):
                # Migrate size
                if field_size is None:
                    if self._columns[column_name]['size']:
                        self.alter_size(column_name, base_type)
                elif self._columns[column_name]['size'] == field_size:
                    pass
                elif (self._columns[column_name]['size']
                        and self._columns[column_name]['size'] < field_size):
                    self.alter_size(column_name, column_type[1])
                else:
                    logger.warning(
                        'Unable to migrate column %s on table %s '
                        'from varchar(%s) to varchar(%s).',
                        column_name, self.table_name,
                        self._columns[column_name]['size'] > 0 and
                        self._columns[column_name]['size'] or "",
                        field_size)
            return

        column_type = column_type[1]
        cursor.execute('ALTER TABLE "%s" ADD COLUMN "%s" %s'
            % (self.table_name, column_name, column_type))
        comment()

        if column_format:
            # check if table is non-empty:
            cursor.execute('SELECT 1 FROM "%s" limit 1' % self.table_name)
            if cursor.rowcount:
                # Populate column with default values:
                default = None
                if default_fun is not None:
                    default = default_fun()
                cursor.execute('UPDATE "' + self.table_name + '" '
                    'SET "' + column_name + '" = %s',
                    (column_format(default),))

        self._update_definitions(columns=True)

    def add_fk(self, column_name, reference, on_delete=None):
        if on_delete is not None:
            on_delete = on_delete.upper()
        else:
            on_delete = 'SET NULL'

        cursor = Transaction().connection.cursor()
        name = self.table_name + '_' + column_name + '_fkey'
        cursor.execute('SELECT 1 '
            'FROM information_schema.key_column_usage '
            'WHERE table_name = %s AND table_schema = %s '
            'AND constraint_name = %s',
            (self.table_name, self.table_schema, name))
        add = False
        if not cursor.rowcount:
            add = True
        elif self._fk_deltypes.get(column_name) != on_delete:
            self.drop_fk(column_name)
            add = True
        if add:
            cursor.execute('ALTER TABLE "' + self.table_name + '" '
                'ADD CONSTRAINT "' + name + '" '
                'FOREIGN KEY ("' + column_name + '") '
                'REFERENCES "' + reference + '" '
                'ON DELETE ' + on_delete)
        self._update_definitions(constraints=True)

    def drop_fk(self, column_name, table=None):
        self.drop_constraint(column_name + '_fkey', table=table)

    def index_action(self, column_name, action='add', table=None):
        if isinstance(column_name, basestring):
            column_name = [column_name]
        index_name = ((table or self.table_name) + "_" + '_'.join(column_name)
            + "_index")
        if self._indexes:
            test_index_name = index_name[:max(map(len, self._indexes))]
        else:
            test_index_name = index_name

        with Transaction().connection.cursor() as cursor:
            if action == 'add':
                if test_index_name in self._indexes:
                    return
                cursor.execute('CREATE INDEX "' + index_name + '" '
                    'ON "' + self.table_name + '" ( '
                        + ','.join(['"' + x + '"' for x in column_name]) + ')')
                self._update_definitions(indexes=True)
            elif action == 'remove':
                if len(column_name) == 1:
                    if (self._field2module.get(column_name[0],
                                self.module_name) != self.module_name):
                        return

                if test_index_name in self._indexes:
                    cursor.execute('DROP INDEX "%s" ' % (index_name,))
                    self._update_definitions(indexes=True)
            else:
                raise Exception('Index action not supported!')

    def not_null_action(self, column_name, action='add'):
        if not self.column_exist(column_name):
            return

        with Transaction().connection.cursor() as cursor:
            if action == 'add':
                if self._columns[column_name]['notnull']:
                    return
                cursor.execute('SELECT id FROM "%s" '
                    'WHERE "%s" IS NULL'
                    % (self.table_name, column_name))
                if not cursor.rowcount:
                    cursor.execute('ALTER TABLE "' + self.table_name + '" '
                        'ALTER COLUMN "' + column_name + '" SET NOT NULL')
                    self._update_definitions(columns=True)
                else:
                    logger.warning(
                        'Unable to set column %s '
                        'of table %s not null !\n'
                        'Try to re-run: '
                        'trytond.py --update=module\n'
                        'If it doesn\'t work, update records '
                        'and execute manually:\n'
                        'ALTER TABLE "%s" ALTER COLUMN "%s" SET NOT NULL',
                        column_name, self.table_name, self.table_name,
                        column_name)
            elif action == 'remove':
                if not self._columns[column_name]['notnull']:
                    return
                if (self._field2module.get(column_name, self.module_name)
                        != self.module_name):
                    return
                cursor.execute('ALTER TABLE "%s" '
                    'ALTER COLUMN "%s" DROP NOT NULL'
                    % (self.table_name, column_name))
                self._update_definitions(columns=True)
            else:
                raise Exception('Not null action not supported!')

    def add_constraint(self, ident, constraint, exception=False):
        ident = self.table_name + "_" + ident
        if ident in self._constraints:
            # This constrain already exist
            return
        cursor = Transaction().connection.cursor()
        try:
            cursor.execute('ALTER TABLE "%s" '
                'ADD CONSTRAINT "%s" %s'
                % (self.table_name, ident, constraint), constraint.params)
        except Exception:
            if exception:
                raise
            logger.warning(
                'unable to add \'%s\' constraint on table %s !\n'
                'If you want to have it, you should update the records '
                'and execute manually:\n'
                'ALTER table "%s" ADD CONSTRAINT "%s" %s',
                constraint, self.table_name, self.table_name, ident,
                constraint)
        self._update_definitions(constraints=True)

    def drop_constraint(self, ident, exception=False, table=None):
        ident = (table or self.table_name) + "_" + ident
        if ident not in self._constraints:
            return
        cursor = Transaction().connection.cursor()
        try:
            cursor.execute('ALTER TABLE "%s" '
                'DROP CONSTRAINT "%s"'
                % (self.table_name, ident))
        except Exception:
            if exception:
                raise
            logger.warning(
                'unable to drop \'%s\' constraint on table %s!',
                ident, self.table_name)
        self._update_definitions(constraints=True)

    def drop_column(self, column_name, exception=False):
        if not self.column_exist(column_name):
            return
        cursor = Transaction().connection.cursor()
        try:
            cursor.execute(
                'ALTER TABLE "%s" DROP COLUMN "%s"' %
                (self.table_name, column_name))

        except Exception:
            if exception:
                raise
            logger.warning(
                'unable to drop \'%s\' column on table %s!',
                column_name, self.table_name, exc_info=True)
        self._update_definitions(columns=True)

    @staticmethod
    def drop_table(model, table, cascade=False):
        cursor = Transaction().connection.cursor()
        cursor.execute('DELETE FROM ir_model_data '
            'WHERE model = \'%s\'' % model)

        query = 'DROP TABLE "%s"' % table
        if cascade:
            query = query + ' CASCADE'
        cursor.execute(query)

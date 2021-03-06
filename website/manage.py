#!/usr/bin/env python3
import argparse
import re
from getpass import getpass

from flask import current_app
from sqlalchemy.exc import IntegrityError, OperationalError

from app import create_app
from database import bdb, remove_model, reset_relational_db, get_column_names, add_column, drop_column, update_column
from database import bdb_refseq
from database import db
from exceptions import ValidationError
from exports.protein_data import EXPORTERS
from helpers.commands import CommandTarget
from helpers.commands import argument
from helpers.commands import argument_parameters
from helpers.commands import command
from helpers.commands import create_command_subparsers
from imports import import_all
from imports.mappings import import_aminoacid_mutation_refseq_mappings
from imports.mappings import import_genome_proteome_mappings
from imports.mutations import MutationImportManager
from imports.mutations import get_proteins
from imports.protein_data import IMPORTERS
from models import Page
from models import User

muts_import_manager = MutationImportManager()
database_binds = ('bio', 'cms')
CONFIG = {'LOAD_STATS': False, 'SCHEDULER_ENABLED': False, 'USE_CELERY': False}


def automigrate(args, app=None):
    if not app:
        app = create_app(config_override=CONFIG)
    for database_bind in args.databases:
        basic_auto_migrate_relational_db(app, bind=database_bind)
    return True


def calc_statistics(args, app=None):
    if not app:
        app = create_app(config_override=CONFIG)
    with app.app_context():
        from stats import Statistics
        statistics = Statistics()
        statistics.calc_all()
        db.session.commit()


def get_all_models(module_name='bio'):
    from models import Model
    from sqlalchemy.ext.declarative.clsregistry import _ModuleMarker
    module_name = 'models.' + module_name

    models = [
        model
        for model in Model._decl_class_registry.values()
        if not isinstance(model, _ModuleMarker) and model.__module__ == module_name
    ]
    return models


def get_answer(question, choices=('y', 'n')):
    while True:
        answer = input('\n' + question + ' (y/n)? ')
        if answer in choices:
            return answer


def basic_auto_migrate_relational_db(app, bind):
    """Inspired with http://stackoverflow.com/questions/2103274/"""

    from sqlalchemy import Table
    from sqlalchemy import MetaData

    print('Performing very simple automigration in', bind, 'database...')
    db.session.commit()
    db.reflect()
    db.session.commit()
    db.create_all(bind=bind)

    with app.app_context():
        engine = db.get_engine(app, bind)
        tables = db.get_tables_for_bind(bind=bind)
        metadata = MetaData()
        metadata.engine = engine

        ddl = engine.dialect.ddl_compiler(engine.dialect, None)

        for table in tables:

            db_table = Table(
                table.name, metadata, autoload=True, autoload_with=engine
            )
            db_columns = get_column_names(db_table)

            columns = get_column_names(table)
            new_columns = columns - db_columns
            unused_columns = db_columns - columns
            existing_columns = columns.intersection(db_columns)

            for column_name in new_columns:
                column = getattr(table.c, column_name)
                if column.constraints:
                    print(
                        'Column %s skipped due to existing constraints.'
                        % column_name
                    )
                    continue
                print('Creating column: %s' % column_name)

                definition = ddl.get_column_specification(column)
                add_column(engine, table.name, definition)

            if engine.dialect.name == 'mysql':
                sql = 'SHOW CREATE TABLE `%s`' % table.name
                table_definition = engine.execute(sql)
                columns_definitions = {}

                to_replace = {
                    'TINYINT(1)': 'BOOL',   # synonymous for MySQL and SQLAlchemy
                    'INT(11)': 'INTEGER',
                    'DOUBLE': 'FLOAT(53)',
                    ' DEFAULT NULL': ''
                }
                for definition in table_definition.first()[1].split('\n'):
                    match = re.match('\s*`(?P<name>.*?)` (?P<definition>[^,]*),?', definition)
                    if match:
                        name = match.group('name')
                        definition_string = match.group('definition').upper()

                        for mysql_explicit_definition, implicit_sqlalchemy in to_replace.items():
                            definition_string = definition_string.replace(mysql_explicit_definition, implicit_sqlalchemy)

                        columns_definitions[name] = name + ' ' + definition_string

                columns_to_update = []
                for column_name in existing_columns:

                    column = getattr(table.c, column_name)
                    old_definition = columns_definitions[column_name]
                    new_definition = ddl.get_column_specification(column)

                    if old_definition != new_definition:
                        columns_to_update.append([column_name, old_definition, new_definition])

                if columns_to_update:
                    print(
                        '\nFollowing columns in `%s` table differ in definitions '
                        'from those in specified in models:' % table.name
                    )
                for column, old_definition, new_definition in columns_to_update:
                    answer = get_answer(
                        'Column: `%s`\n'
                        'Old definition: %s\n'
                        'New definition: %s\n'
                        'Update column definition?'
                        % (column, old_definition, new_definition)
                    )
                    if answer == 'y':
                        update_column(engine, table.name, new_definition)
                        print('Updated %s column definition' % column)
                    else:
                        print('Skipped %s column' % column)

            if unused_columns:
                print(
                    '\nFollowing columns in `%s` table are no longer used '
                    'and can be safely removed:' % table.name
                )
                for column in unused_columns:
                    answer = get_answer('Column: `%s` - remove?' % column)
                    if answer == 'y':
                        drop_column(engine, table.name, column)
                        print('Removed column %s.' % column)
                    else:
                        print('Keeping column %s.' % column)

    print('Automigration of', bind, 'database completed.')


class CMS(CommandTarget):

    description = 'should Content Management System database be {command}ed'

    @command
    def load(args):
        content = """
        <ul>
            <li><a href="/search/proteins">search for a protein</a>
            <li><a href="/search/mutations">search for mutations</a>
        </ul>
        """
        main_page = Page(
            content=content,
            title='Visualisation Framework for Genome Mutations',
            address='index'
        )
        db.session.add(main_page)
        print('Index page created')
        print('Creating root user account')

        correct = False

        while not correct:
            try:
                email = input('Please type root email: ')
                password = getpass(
                    'Please type root password (you will not see characters '
                    'you type due to security reasons): '
                )
                root = User(email, password, access_level=10)
                root.is_verified = True
                correct = True
            except ValidationError as e:
                print('Root credentials are incorrect: ', e.message)
                print('Please, try to use something different or more secure:')
            except IntegrityError:
                db.session.rollback()
                print(
                    'IntegrityError: either a user with this email already '
                    'exists or there is a serious problem with the database. '
                    'Try to use a different email address'
                )

        db.session.add(root)
        db.session.commit()
        print('Root user with email', email, 'created')

        print('Root user account created')

    @command
    def remove(args):
        reset_relational_db(current_app, bind='cms')


class ProteinRelated(CommandTarget):

    description = (
        'should chosen by the User part of biological database'
        'be {command}ed'
    )

    @command
    def load_all(args):
        import_all()

    @command
    def load(args):
        data_importers = IMPORTERS
        for importer_name in args.importers:
            importer = data_importers[importer_name]
            print('Running {name}:'.format(name=importer_name))
            results = importer()
            if results:
                db.session.add_all(results)
            db.session.commit()

    @load.argument
    def importers():
        data_importers = IMPORTERS
        return argument_parameters(
            '-i',
            '--importers',
            nargs='*',
            help=(
                'What should be imported?'
                ' Available choices are: ' +
                ', '.join(data_importers) + '.'
                ' By default all data will be imported.'
                ' The order of imports matters; preferable order'
                ' is the same as order of choices listed above.'
            ),
            choices=data_importers,
            metavar='',
            default=data_importers,
        )

    @command
    def export(args):
        exporters = EXPORTERS
        if args.paths and len(args.paths) != len(args.exporters):
            print('Export paths should be given for every exported file, no less, no more.')
            return
        kwargs = {}
        for i, name in enumerate(args.exporters):
            exporter = exporters[name]
            if args.paths:
                kwargs['path'] = args.paths[i]
            out_file = exporter(**kwargs)
            print('Exported %s to %s' % (name, out_file))

    @export.argument
    def exporters():
        data_exporters = EXPORTERS
        return argument_parameters(
            '-e',
            '--exporters',
            nargs='*',
            help=(
                'What should be exported?'
                ' Available: ' + ', '.join(data_exporters) + '.'
                ' By default everything will be exported.'
            ),
            choices=data_exporters,
            metavar='',
            default=data_exporters,
        )

    @export.argument
    def paths():
        return argument_parameters(
            '--paths',
            nargs='*',
            metavar='',
            help='A path(s) for export file(s)',
        )

    @command
    def remove_all(args):
        reset_relational_db(current_app, bind='bio')

    @command
    def remove(args):
        import models.bio as bio_models
        for model_name in args.models:
            model = getattr(bio_models, model_name)
            remove_model(model)
            db.session.commit()

    @remove.argument
    def models():

        models = get_all_models('bio')

        models_names = [model.__name__ for model in models]

        return argument_parameters(
            '--models', '-m',
            nargs='+',
            metavar='',
            help=(
                'Names of models to be removed.'
                ' Available models: ' + ', '.join(models_names) + '.'
            ),
            choices=models_names
        )


class Mappings(CommandTarget):

    description = 'should mappings (DNA -> protein) be {command}ed'

    @command
    def load(args):
        print('Importing %s mappings' % (args.restrict_to or 'all'))
        proteins = get_proteins()

        if args.restrict_to != 'aminoacid_refseq':
            import_genome_proteome_mappings(proteins, bdb_dir=args.path)
        if args.restrict_to != 'genome_proteome':
            import_aminoacid_mutation_refseq_mappings(proteins, bdb_dir=args.path)

    @load.argument
    def restrict_to():
        return argument_parameters(
            '--restrict_to', '-r',
            default=None,
            choices=['genome_proteome', 'aminoacid_refseq'],
            help='Should only genome_proteome or aminoacid_refseq mappings be imported?'
        )

    @load.argument
    def path():
        return argument_parameters(
            '--path',
            type=str,
            default='',
            help='A path to dir where mappings dbs should be created'
        )

    @command
    def remove(args):
        print('Removing mappings database...')
        bdb.reset()
        bdb_refseq.reset()
        print('Removing mappings database completed.')


class Mutations(CommandTarget):

    description = 'should only mutations be {command}ed without db restart'

    @staticmethod
    def action(name, args):
        proteins = get_proteins()
        kwargs = vars(args)
        if 'func' in kwargs:
            kwargs.pop('func')
        if 'type' in kwargs:
            kwargs.pop('type')
        muts_import_manager.perform(
            name, proteins, **kwargs
        )

    @command
    def load(args):
        Mutations.action('load', args)

    @command
    def remove(args):
        Mutations.action('remove', args)

    @command
    def export(args):
        if args.type == 'proteome':
            Mutations.action('export', args)
        else:
            assert args.type == 'genomic_ptm'
            Mutations.action('export_genomic_coordinates_of_ptm', args)

    @command
    def update(args):
        Mutations.action('update', args)

    @argument
    def sources():
        mutation_importers = muts_import_manager.names

        return argument_parameters(
            '-s',
            '--sources',
            nargs='*',
            help=(
                'Which mutations should be loaded or removed?'
                ' Available sources are: ' +
                ', '.join(mutation_importers) + '.'
                ' By default all sources will be used.'
            ),
            choices=mutation_importers,
            metavar='',
            default=mutation_importers
        )

    @export.argument
    def only_primary_isoforms():
        return argument_parameters(
            '-o',
            '--only_primary_isoforms',
            action='store_true',
            help='Restrict export to primary isoforms',
        )

    @export.argument
    def type():
        return argument_parameters(
            '-t',
            '--type',
            choices=['proteomic', 'genomic_ptm'],
            help='What type of mutations should be exported: proteomic or genomic_ptm (genomic affecting PTM).'
                 'By default proteomic mutations will be exported',
        )


class All(CommandTarget):

    description = 'should everything be {command}ed'

    @command
    def load(args):
        ProteinRelated.load_all(args)
        Mutations.load(argparse.Namespace(sources='__all__'))
        Mappings.load(args)
        CMS.load(args)

    @command
    def remove(args):
        ProteinRelated.remove_all(args)
        Mutations.remove(argparse.Namespace(sources='__all__'))
        Mappings.remove(args)
        CMS.remove(args)


def new_subparser(subparsers, name, func, **kwargs):
    subparser = subparsers.add_parser(name, **kwargs)
    subparser.set_defaults(func=func)
    return subparser


def run_shell(args):
    print('Starting interactive shell...')
    app = create_app(config_override=CONFIG)
    with app.app_context():
        import stats
        import models

        if args.command:
            print('Executing supplied command: "%s"' % args.command)
            exec(args.command)

        print('You can access current application using "app" variable.')
        print('Database, models and statistics modules are pre-loaded.')

        fallback = False
        if not args.raw:
            try:
                from IPython import embed
                embed()
            except ImportError:
                print('To use enhanced interactive shell install ipython3')
                fallback = True

        if fallback or args.raw:
            import code
            all_vars = locals()
            all_vars.update(vars(models))
            code.interact(local=all_vars)


def create_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(help='sub-commands')

    data_subcommands = ['load', 'remove', 'export', 'update']

    command_subparsers = {
        subcommand: subparsers.add_parser(
            subcommand,
            help='{0} data from specified category'.format(subcommand)
        )
        for subcommand in data_subcommands
    }

    create_command_subparsers(command_subparsers)

    new_subparser(
        subparsers,
        'calc_stats',
        calc_statistics,
        help=(
            'should statistics (counts of protein, pathways, mutation, etc) be recalculated?'
        )
    )

    shell_parser = new_subparser(
        subparsers,
        'shell',
        run_shell
    )

    shell_parser.add_argument(
        '-r',
        '--raw',
        action='store_true'
    )

    shell_parser.add_argument(
        '-c',
        '--command',
        type=str
    )

    migrate_parser = new_subparser(
        subparsers,
        'migrate',
        automigrate,
        help=(
            'should a basic auto migration on relational databases'
            'be performed? It will only create new tables'
        )
    )

    migrate_parser.add_argument(
        '-d',
        '--databases',
        type=str,
        nargs='*',
        choices=database_binds,
        default=database_binds,
        help=(
            'which databases should be automigrated?'
            ' Possible values: ' + ', '.join(database_binds) + ' '
            'By default all binds will be used.'
        )
    )
    return parser


def run_manage(parsed_args, app=None):
    if not hasattr(parsed_args, 'func'):
        print('Scripts loaded successfully, no tasks specified.')
        return

    if not app:
        try:
            app = create_app(config_override=CONFIG)
        except OperationalError as e:
            if e.orig.args[0] == 1071:
                print('Please run: ')
                print('ALTER DATABASE `db_bio` CHARACTER SET utf8;')
                print('ALTER DATABASE `db_cms` CHARACTER SET utf8;')
                print('to be able to continue.')
                return
            else:
                raise

    with app.app_context():
        parsed_args.func(parsed_args)

    print('Done, all tasks completed.')


if __name__ == '__main__':
    parser = create_parser()

    args = parser.parse_args()
    run_manage(args)

else:
    print('This script should be run from command line')

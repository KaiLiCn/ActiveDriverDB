import operator
import random
from collections import namedtuple
from functools import reduce
from statistics import median
from typing import List

from sqlalchemy import and_, func, distinct, desc
from sqlalchemy.orm import aliased
from tqdm import tqdm

from database import join_unique, db
from models import Protein, Mutation, The1000GenomesMutation, MC3Mutation, InheritedMutation, Gene, Site, MutationSource


def count_mutated_potential_sites():
    count = 0
    total_length = 0
    for protein in tqdm(Protein.query, total=Protein.query.count()):
        mutated_positions = set()
        for mut in protein.confirmed_mutations:
            mutated_positions.update(range(mut.position - 7, mut.position + 7))
        total_length += protein.length

        for i in range(protein.length):
            if i in mutated_positions:
                count += 1
    print(count, total_length, count/total_length*100)


def test_enrichment_of_ptm_mutations_among_mutations_subset(subset_query, reference_query, iterations_count=100000, subset_size=None, subset_ptms=None):
    """Perform tests according to proposed algorithm:

    1. Count the number of all ClinVar mutations as C, PTM-associated ClinVar mutations as D=27071
        and percentage of the latter as E=19%.
    2. Randomly draw C mutations from 1,000 Genomes
    3. Record the number and % of those N mutations that affect PTM sites, as P, Q
    4. Repeat 2-3 for M=10,000 times
    5. Count in how many of M iterations does D>P and E>Q. These percentages make up the permutation test p-values
    6. Repeat with TCGA instead of ClinVar.


    Args:
        subset_query:
            SQLAlchemy query yielding mutation dataset
            to test (e.g. ClinVar or TCGA)

        reference_query:
            query yielding a population dataset to test
            against (to be used as a reference distribution
            e.g. 1000 Genomes)

    Returns:
        namedtuple with:
            median,
            median percentage,
            p_value: 1 - p-value,
            enriched_ptm_muts_in_iterations: list of counts of PTM sites discovered in each of sampling iterations,
            expected_ptm_muts:  expected number of mutations associated with PTM sites
    """
    ptm_enriched_absolute = 0
    ptm_enriched_percentage = 0

    is_ptm = Mutation.precomputed_is_ptm

    # 1.
    if subset_query:
        all_mutations = subset_query.count()                        # C
        ptm_mutations = subset_query.filter(is_ptm).count()         # D
    else:
        assert subset_size and subset_ptms
        all_mutations = subset_size
        ptm_mutations = subset_ptms

    ptm_percentage = ptm_mutations / all_mutations * 100        # E

    print('Counting enrichment in random subsets of background.')
    print('All: %s, PTM: %s, %%: %s' % (all_mutations, ptm_mutations, ptm_percentage))

    all_reference_mutations = reference_query.all()

    enriched_ptms = []
    enriched_percentage = []

    # 4.
    for _ in tqdm(range(iterations_count)):
        # 2.
        random_reference = random.sample(all_reference_mutations, all_mutations)

        # 3.
        all_in_iteration = len(random_reference)
        ptm_in_iteration = sum(1 for mutation in random_reference if mutation.precomputed_is_ptm)  # P
        iteration_percentage = ptm_in_iteration / all_in_iteration * 100                           # Q

        assert all_in_iteration == all_mutations

        # 5.
        if ptm_mutations > ptm_in_iteration:        # D > P
            ptm_enriched_absolute += 1
        if ptm_percentage > iteration_percentage:   # E > Q
            ptm_enriched_percentage += 1

        enriched_ptms.append(ptm_in_iteration)
        enriched_percentage.append(iteration_percentage)

    median_ptms = median(enriched_ptms)
    median_percentage = median(enriched_percentage)

    result_tuple = namedtuple(
        'EnrichmentAnalysisResult',
        'enriched_ptm_muts_in_iterations, '
        'median, median_percentage, '
        'p_value, p_value_percentage, '
        'observed_ptm_muts'
    )

    return result_tuple(
        enriched_ptms,
        median_ptms,
        median_percentage,
        ptm_enriched_absolute / iterations_count,
        ptm_enriched_percentage / iterations_count,
        ptm_mutations
    )


def get_confirmed_mutations(
        sources, only_preferred=True, genes=None, confirmed_by_definition=False,
        base_query=None
    ):
    """
    Utility to generate a query for retrieving confirmed mutations having specific mutation details.

    Args:
        sources: list of mutation details (sources) to be used to filter
            the mutations (including sources with non-confirmed mutations)
        only_preferred: include only mutations from preferred isoforms
        genes: limit to genes from provided list
        confirmed_by_definition: do not apply the expensive is_confirmed=True
            filter as all sources include only confirmed mutations
        base_query: the initial mutation query (allows to adjust selected columns)

    Returns:
        Query object yielding mutations.
    """

    if not base_query:
        base_query = Mutation.query

    mutations = base_query

    def only_from_primary_isoforms(mutations_query):

        mutations_query = join_unique(mutations_query, Protein)
        return mutations_query.filter(Protein.is_preferred_isoform)

    if not confirmed_by_definition:
        mutations = mutations.filter_by(is_confirmed=True)

    # TODO: remove?
    mutations = only_from_primary_isoforms(mutations)

    if genes:
        mutations = mutations.filter(Protein.id.in_([g.preferred_isoform_id for g in genes]))

    selected_mutations = mutations.filter(Mutation.in_sources(*sources))

    if only_preferred:
        selected_mutations = only_from_primary_isoforms(selected_mutations)

    return selected_mutations


def test_ptm_enrichment():
    # 1000 Genomes
    ref_genes = get_genes_with_mutations_from_sources([The1000GenomesMutation], only_genes_with_ptm_sites=True)

    # TCGA against 1000Genomes
    cancer_genes = load_cancer_census()
    tcga_result = parametric_test_ptm_enrichment(MC3Mutation, The1000GenomesMutation, cancer_genes, ref_genes)

    # ClinVar against 1000Genomes
    tested_genes = get_genes_with_mutations_from_sources([InheritedMutation], only_genes_with_ptm_sites=True)
    clinvar_result = parametric_test_ptm_enrichment(InheritedMutation, The1000GenomesMutation, tested_genes, ref_genes)

    return clinvar_result, tcga_result


def test_enrichment_against_source(ptm_muts_count: int, all_muts_count: int, source=The1000GenomesMutation):
    ref_genes = get_genes_with_mutations_from_sources([source], only_genes_with_ptm_sites=True)
    reference_mutations = get_confirmed_mutations([source], genes=ref_genes)

    result = test_enrichment_of_ptm_mutations_among_mutations_subset(
        None, reference_mutations, subset_size=all_muts_count, subset_ptms=ptm_muts_count
    )

    return result


def parametric_test_ptm_enrichment(tested_source, reference_source, tested_genes, ref_genes):
    """Uses only mutations from primary isoforms."""

    # e.g. 1000Genomes
    reference_mutations = get_confirmed_mutations([reference_source], genes=ref_genes)

    # e.g. ClinVar
    tested_mutations = get_confirmed_mutations([tested_source], genes=tested_genes)

    result = test_enrichment_of_ptm_mutations_among_mutations_subset(tested_mutations, reference_mutations)

    return result


def non_parametric_test_ptm_enrichment():
    """Uses only mutations from primary isoforms.

    Use wilcox.test from R to compare distributions
    of PTM affecting/all mutations between clinvar
    and 1000 Genomes Project mutation datasets.
    """
    from rpy2.robjects import r
    from rpy2.robjects import FloatVector

    def collect_ratios(sources, only_genes_with_ptm_sites=False):
        ratios = []
        genes = get_genes_with_mutations_from_sources(sources, only_genes_with_ptm_sites)

        print('Number of genes:', len(genes))

        for gene in tqdm(genes):
            protein = gene.preferred_isoform
            filters = and_(
                Mutation.protein == protein,
                Mutation.is_confirmed == True
            )
            number_of_all_mutations = Mutation.query.filter(filters).count()
            number_of_ptm_mutations = Mutation.query.filter(and_(
                filters,
                Mutation.precomputed_is_ptm == True
            )).count()
            ratios.append(number_of_ptm_mutations/number_of_all_mutations)
        return FloatVector(ratios)

    results = []
    wilcox = r['wilcox.test']

    for exclude_no_ptms in [True, False]:
        print('Genes with no PTM sites excluded?', exclude_no_ptms)

        ratios_clinvar = collect_ratios([InheritedMutation], exclude_no_ptms)
        ratios_tkgenomes = collect_ratios([The1000GenomesMutation], exclude_no_ptms)

        result = wilcox(ratios_clinvar, ratios_tkgenomes, alternative='greater')
        print('Clinvar / 1000Genomes', result)

        results.append(result)

        ratios_both = collect_ratios([InheritedMutation, The1000GenomesMutation], exclude_no_ptms)
        result = wilcox(ratios_both, ratios_tkgenomes, alternative='greater')
        print('1000Genomes & Clinvar / 100Genomes', result)
        results.append(result)

    return results


def load_cancer_census(cancer_census_path='data/disease_muts_in_ptm_sites/census.tsv'):
    """Load genes from cancer census.

    Args:
        cancer_census_path: this file needs to be downloaded from COSMIC
    """

    gene_names = set()
    with open(cancer_census_path) as f:
        for line in f:
            gene_names.add(line.split('\t')[0])

    gene_names.remove('Gene Symbol')

    cancer_genes = set()

    for name in gene_names:
        g = Gene.query.filter_by(name=name).first()
        if g:
            cancer_genes.add(g)
        else:
            print(f'Cancer Census gene: "{name}" not in database')
    return cancer_genes


def get_genes_with_mutations_from_sources(sources, only_genes_with_ptm_sites=False):
    query = (
        db.session.query(Gene)
        .join(Protein, Gene.preferred_isoform_id == Protein.id)
        .join(Mutation)
    )
    query = query.filter(Mutation.in_sources(*sources))

    genes = set(query.distinct())

    if only_genes_with_ptm_sites:
        return {
            gene
            for gene in genes
            if gene.preferred_isoform.sites
        }
    return genes


def count_mutations_from_genes(genes, sources, only_preferred_isoforms=False, strict=True):
    """Counts mutations and PTM mutations from isoforms from given set of genes.

    Args:
        genes: a list of Gene
        only_preferred_isoforms: should only one isoform per gene
            (the preferred/primary one) be used when filtering mutations?
        sources: a list of MutationDetails - only confirmed mutations from
            sources identified by given MutationDetail classes will be counted
    """
    all_mutations_count = 0
    ptm_mutations_count = 0

    if strict:
        base_query = (
            db.session.query(
                Mutation.position,
                Mutation.alt,
                Protein.id
            )
            .select_from(Mutation)
            .join(Protein)
        )
    else:
        base_query = Mutation.query

    for gene in tqdm(genes):
        if only_preferred_isoforms:
            proteins = [gene.preferred_isoform]
        else:
            proteins = gene.isoforms

        mutations_filters = and_(
            Mutation.protein_id.in_([p.id for p in proteins]),
            Mutation.is_confirmed == True,
            Mutation.in_sources(*sources)
        )

        all_mutations_count += (
            base_query
            .filter(mutations_filters)
            .distinct().count()
        )

        ptm_mutations_count += (
            base_query
            .filter(and_(
                Mutation.precomputed_is_ptm,
                mutations_filters
            )).distinct().count()
        )

    print(
        all_mutations_count,
        ptm_mutations_count,
        ptm_mutations_count / all_mutations_count
    )
    return all_mutations_count, ptm_mutations_count


def disease_muts_affecting_ptm_sites():
    cancer_genes = load_cancer_census()

    clinvar_genes = get_genes_with_mutations_from_sources([InheritedMutation])

    for only_preferred_isoforms in [True, False]:
        print('Only preferred isoforms:', only_preferred_isoforms)
        print('ClinVar/ClinVar')
        count_mutations_from_genes(clinvar_genes, [InheritedMutation], only_preferred_isoforms)
        print('Cancer census/TCGA')
        count_mutations_from_genes(cancer_genes, [MC3Mutation], only_preferred_isoforms)


def prepare_for_summing(sources: List[MutationSource], count_distinct_substitutions=False) -> List:
    """Mutations from various sources can be summed up differently.

    Given list of mutation sources, checks if the mutation sources
    can be summed up (counted together) in a reasonable way.

    It is possible - though not always advisable - to add frequencies
    of different types of population mutations; it is also possible to
    add occurrence counts of ClinVar and TCGA mutations;

    It is assumed that counting population and non-population
    mutations together is not reasonable as summing frequencies
    with counts has no meaning; however, counting (and summing)
    unique occurrences of mutations from incompatible mutation
    sources is possible with `count_distinct_substitutions=True`.

    Returns:
        list of expressions counting mutations from provided sources
    """

    if count_distinct_substitutions:
        counts = [func.count(distinct(source.id)) for source in sources]
    else:
        source = sources[0]

        if hasattr(source, 'count'):
            assert all(hasattr(s, 'count') for s in sources)
            counts = [
                func.sum(source.count)
                for source in sources
            ]
        elif hasattr(source, 'maf_all'):
            assert all(hasattr(s, 'maf_all') for s in sources)
            counts = [
                func.sum(source.maf_all)
                for source in sources
            ]
        else:
            raise Exception(
                f'Undefined behaviour for summing: {source} source,'
                f' which has neither `count` nor `maf_all` defined.'
            )

    return counts


def most_mutated_sites(sources: List[MutationSource], site_type=None, limit=25, muts_intersection=True):
    """Sources must be of the same type"""

    counts = prepare_for_summing(sources)

    query = (
        db.session.query(
            Site,
            *[
                count.label(f'count_{i}')
                for i, count in enumerate(counts)
            ]
        )
        .select_from(Mutation)
    )

    if muts_intersection:
        for source in sources:
            query = query.join(source)
    else:
        for source in sources:
            query = query.outerjoin(source)

    query = (
        query
        .join(Mutation.affected_sites)
        .filter(Site.protein.has(Protein.is_preferred_isoform))
    )

    if site_type:
        query = query.filter(Site.type.contains(site_type))

    query = (
        query
        .group_by(Site)
        .having(and_(*counts))
    )

    if len(sources) > 1:
        # this code will work when len(sources) == 1,
        # but using subquery might be suboptimal;

        # the only downside is that the label of
        # the count is then 'count_1' instead of
        # 'mutations_count'

        query = query.subquery()

        total_muts_count = reduce(
            operator.add, [
                getattr(query.c, f'count_{i}')
                for i in range(len(counts))
            ]
        )

        total_muts_count = total_muts_count.label('mutations_count')

        query = (
            db.session.query(
                aliased(Site, query),
                total_muts_count,
            )
            .order_by(desc(total_muts_count))
        )

    return query.limit(limit)

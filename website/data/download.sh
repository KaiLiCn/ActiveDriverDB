#!/usr/bin/env bash
for required_program in 'wget' 'unzip' 'Rscript' 'tar' 'synapse'
do
  hash $required_program 2>/dev/null || {
    echo >&2 "$required_program is required but it is not installed. Aborting."
    exit 1
  }
done

wget https://gist.githubusercontent.com/krassowski/8c9710fa20ac944ec8d47ac4a0ac5b4a/raw/444fcc584bc10b5e504c05a6063a281cee808c9c/ucsc_download.sh
source ucsc_download.sh
# refseq mRNA summaries
get_whole_genome_table refseq_summary.tsv.gz genes refGene hgFixed.refSeqSummary gzip
# some of protein mappings (RefSeq)
get_whole_genome_table refseq_link.tsv.gz genes refGene hgFixed.refLink gzip
rm ucsc_download.sh

# full gene name
wget ftp://ftp.ncbi.nlm.nih.gov/gene/DATA/GENE_INFO/Mammalia/Homo_sapiens.gene_info.gz

# hierarchy tree
wget ftp://ftp.ebi.ac.uk/pub/databases/interpro/ParentChildTreeFile.txt

# entry list (all data)
wget ftp://ftp.ebi.ac.uk/pub/databases/interpro/interpro.xml.gz

# protein mappings (external references)
wget ftp://ftp.ncbi.nlm.nih.gov/refseq/H_sapiens/RefSeqGene/LRG_RefSeqGene
wget ftp://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/idmapping/by_organism/HUMAN_9606_idmapping.dat.gz

# pathway list
wget http://biit.cs.ut.ee/gprofiler/gmt/gprofiler_hsapiens.NAME.gmt.zip
unzip gprofiler_hsapiens.NAME.gmt.zip
mv gprofiler_hsapiens.NAME.gmt/hsapiens.pathways.NAME.gmt .
rm gprofiler_hsapiens.NAME.gmt.zip
rm -r gprofiler_hsapiens.NAME.gmt
rm hsapiens.OMIM.NAME.gmt
rm hsapiens.MI.NAME.gmt
rm hsapiens.HPA.NAME.gmt
rm hsapiens.HP.NAME.gmt
rm hsapiens.GO.NAME.gmt
rm hsapiens.CORUM.NAME.gmt
rm hsapiens.BIOGRID.NAME.gmt
rm hsapiens.REAC.NAME.gmt
rm hsapiens.TF.NAME.gmt

#  All below are dropbox-dependent ===

wget https://www.dropbox.com/s/6s6mni9x40gphta/PTM_site_table.rsav
R --no-save <<-SCRIPT
load('PTM_site_table.rsav')
write.table(
site_table, file='site_table.tsv',
row.names=F, quote=F, sep='\t'
)
SCRIPT
rm PTM_site_table.rsav

wget https://www.dropbox.com/s/wdxnyvf7lkbihnp/biomart_protein_domains_20072016.txt
wget https://www.dropbox.com/s/pjf7nheutez3w6r/curated_kinase_IDs.txt

echo 'Downloading mutations:'

mkdir -p mutations
cd mutations

#echo "Please, enter your synapse credentials to download MC3 dataset"
#read -p "Login: " synapse_login
#read -s -p "Password: " synapse_password
#synapse login -u $synapse_login -p $synapse_password
#synapse get syn7824274
#echo "MC3 mutations dataset downloaded"

wget https://www.dropbox.com/s/lhou9rnwl6lwuwj/mc3.v0.2.8.PUBLIC.maf.gz
wget https://www.dropbox.com/s/zodasbvinx339tw/ESP6500_muts_annotated.txt.gz
wget https://www.dropbox.com/s/du2qe1skxwmuep2/clinvar_muts_annotated.txt.gz
wget https://www.dropbox.com/s/pm74k3qwxrqmu2q/all_mimp_annotations_p085.rsav

echo 'Extracting MIMP mutations from .rsav file... (it will take a long time)'

Rscript -e 'load("all_mimp_annotations_p085.rsav");write.table(all_mimp_annotations, file="all_mimp_annotations.tsv", row.names=F, quote=F, sep="\t");'
rm all_mimp_annotations_p085.rsav

mkdir -p G1000
cd G1000
wget https://www.dropbox.com/s/fidorbveacpo0yh/G1000.hg19_multianno_nsSNV.tgz

echo 'unpacking...'
tar -xvzf G1000.hg19_multianno_nsSNV.tgz
rm G1000.hg19_multianno_nsSNV.tgz
cd ..

cd ..

mkdir -p MIMP_logos
cd MIMP_logos
echo 'Downloading MIMP logos:'
wget https://www.dropbox.com/s/3gjkaim1qs8xc2o/MIMP_logos.zip
unzip MIMP_logos.zip
rm MIMP_logos.zip

cd ..

echo "Getting list of words we don't wont to have as autogenerated shorthands:"
wget https://raw.githubusercontent.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/en -O bad-words.txt

echo 'Downloading all posible SNVs:'
wget https://www.dropbox.com/s/qtuqvucb8nzim51/ALL_PROTEIN_ANNOT.tgz

echo 'unpacking...'
tar -xvzf ALL_PROTEIN_ANNOT.tgz
rm ALL_PROTEIN_ANNOT.tgz


# remove temporary dir
rm -r tmp

cd mutations
./annotate_mc3.sh

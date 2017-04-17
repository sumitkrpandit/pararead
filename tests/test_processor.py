""" Basic tests for ParaRead """

import itertools
import os

import pytest
from pysam import AlignmentFile

from pararead.exceptions import \
    CommandOrderException, IllegalChunkException, \
    MissingOutputFileException
from pararead.processor import ParaReadProcessor
from tests import \
    NUM_CORES_DEFAULT, NUM_READS_BY_FILE, \
    PATH_ALIGNED_FILE, PATH_UNALIGNED_FILE
from tests.helpers import IdentityProcessor, loglines


__author__ = "Vince Reuter"
__email__ = "vreuter@virginia.edu"



class ConstructorTests:
    """
    Basic tests for ParaReadProcessor.
    """

    def test_is_abstract(self):
        """ ParaReadProcessor must be extended. """
        with pytest.raises(TypeError) as exc:
            # Provide filler arguments for ParaReadProcessor parameters
            # in an effort to ensure that the TypeError comes from the
            # requirement that the class is abstract, rather than from
            # missing arguments for required parameters.
            ParaReadProcessor(path_reads_file="dummy.bam",
                              cores=NUM_CORES_DEFAULT, outfile="dummy.txt")
        # As a fallback, check that the exception message mentions "abstract."
        assert "abstract" in exc.value.message


    @pytest.mark.parametrize(
            argnames="filepath",
            argvalues=[PATH_ALIGNED_FILE, PATH_UNALIGNED_FILE])
    def test_requires_outfile_or_action(self, filepath):
        """ Explicit output file or action name to derive one is needed. """
        with pytest.raises(ValueError):
            IdentityProcessor(filepath)



class FileRegistrationTests:
    """ Tests for registration of files with the ParaReadProcessor. """


    @pytest.mark.parametrize(
            argnames="require_aligned", argvalues=[False, True])
    @pytest.mark.parametrize(
            argnames="pysam_kwargs", argvalues=[{}, {"check_sq": False}])
    def test_adds_pysam_kwargs(self, require_aligned,
                               pysam_kwargs, remove_reads_file):
        """ Unaligned input BAM needs check_sq=False to be created. """

        # Note that remove_reads_file is here to clear the module-scoped map.

        # Explicitly set by_chromosome=False to prevent it from
        # controlling the requirement regarding aligned reads.
        processor = IdentityProcessor(
                path_reads_file=PATH_UNALIGNED_FILE, action="test",
                allow_unaligned=not require_aligned, by_chromosome=False)

        if require_aligned and not pysam_kwargs:
            with pytest.raises(ValueError):
                processor.register_files()
        else:
            # No exception --> pass (file registration is just for effect.)
            processor.register_files(**pysam_kwargs)


    @pytest.mark.parametrize(
        argnames=["path_reads_file", "require_aligned"],
        argvalues=[(PATH_ALIGNED_FILE, False), (PATH_ALIGNED_FILE, True),
                   (PATH_UNALIGNED_FILE, False)],
        ids=lambda (rf_path, req_align):
                    "{}; req_align={}".format(rf_path, req_align))
    def test_creates_fresh_reads_file(self, path_reads_file,
                                      require_aligned, remove_reads_file):
        """ Reads file pysam object is created by register_files(). """

        # Note that remove_reads_file is here to clear the module-scoped map.

        # Explicitly set by_chromosome=False to prevent it from
        # controlling the requirement regarding aligned reads.
        processor = IdentityProcessor(
                path_reads_file=path_reads_file, action="test",
                allow_unaligned=not require_aligned, by_chromosome=False)

        # The pysam readsfile shouldn't exist before register_files().
        with pytest.raises(CommandOrderException):
            processor.readsfile()

        # Now do the registration, creating the pysam readsfile instance.
        processor.register_files()
        readsfile = processor.readsfile()

        # Check out the new readsfile.
        assert isinstance(readsfile, AlignmentFile)
        num_reads = sum(1 for _ in readsfile)
        assert NUM_READS_BY_FILE[path_reads_file] == num_reads



class CombinerTests:
    """ Processor provides function to combine intermediate results. """

    # NOTE: seemingly-unused fixtures are present for effectful-ness.

    CHROMOSOME_CHUNK_KEY = "chromosome"
    ARBITRARY_CHUNK_KEY = "arbitrary"
    CHROM_NAMES = ["chr{}".format(i) for i in range(1, 23)] + \
                  ["chrX", "chrY", "chrM"]
    ARBITRARY_NAMES = ["random0", "arbitrary1", "contig2"]
    CHUNK_NAMES = {CHROMOSOME_CHUNK_KEY: CHROM_NAMES,
                   ARBITRARY_CHUNK_KEY: ARBITRARY_NAMES}


    @pytest.fixture(scope="function")
    def extant_files(self, request, tmpdir):
        """
        Ensure the existence of certain files for a test case.
        
        Parameters
        ----------
        request : pytest.fixtures.SubRequest
            Test case requesting the parameterization.
        tmpdir : py._path.local.LocalPath
            Path to temporary folder for the test case.

        """

        if "which_names" in request.fixturenames:
            chunk_names_key = request.getfixturevalue("which_names")
            chunk_names = self._names_from_key(chunk_names_key)
        else:
            chunk_names = self.CHROM_NAMES

        if "filetype" in request.fixturenames:
            extension = request.getfixturevalue("filetype")
        else:
            extension = "txt"

        files = []
        for chunk in chunk_names:
            path_out_file = tmpdir.join("{}.{}".format(chunk, extension))
            path_out_file.ensure(file=True)
            files.append(path_out_file.strpath)
        return files


    @pytest.fixture(scope="function")
    def fixed_tempfolder_processor(self, tmpdir, num_cores):
        path_output_file = tmpdir.join("test-output.txt").strpath
        processor = IdentityProcessor(
            PATH_ALIGNED_FILE, cores=num_cores, outfile=path_output_file)
        processor.temp_folder = tmpdir.strpath
        return processor


    @pytest.mark.parametrize(
            argnames="error_if_missing", argvalues=[False, True])
    def test_nothing_to_combine(self, tmpdir, path_logs_file,
                                num_cores, error_if_missing):
        """ Complete lack of output is sufficient to warrant a warning. """

        # Create the processor and do combine() step.
        path_output_file = tmpdir.join("output.txt").strpath
        processor = IdentityProcessor(
                PATH_ALIGNED_FILE, cores=num_cores, outfile=path_output_file)

        num_logs_before_combine = len(loglines(path_logs_file))

        processor.combine(good_chromosomes=[], strict=error_if_missing)
        # The log record should be a warning, and there's only one.
        log_records = loglines(path_logs_file)

        assert 1 == len(log_records) - num_logs_before_combine
        assert "WARN" in log_records[num_logs_before_combine]


    @pytest.mark.parametrize(
            argnames="which_names",
            argvalues=[CHROMOSOME_CHUNK_KEY, ARBITRARY_CHUNK_KEY])
    def test_missing_output_files(
            self, which_names, extant_files, fixed_tempfolder_processor):
        """ Missing-output chunks be skipped or exceptional. """
        combination_request_names = self.CHROM_NAMES + self.ARBITRARY_NAMES
        with pytest.raises(MissingOutputFileException):
            fixed_tempfolder_processor.combine(combination_request_names, strict=True)


    @pytest.mark.parametrize(
        argnames="which_names",
        argvalues=[CHROMOSOME_CHUNK_KEY, ARBITRARY_CHUNK_KEY])
    def test_missing_output_files_non_strict_retval(
            self, which_names, extant_files,
            fixed_tempfolder_processor, path_logs_file):
        combination_request_names = self.CHROM_NAMES + self.ARBITRARY_NAMES
        observed_combined_filepaths = fixed_tempfolder_processor.combine(
                combination_request_names, strict=False)
        assert extant_files == observed_combined_filepaths


    @pytest.mark.parametrize(
        argnames="which_names",
        argvalues=[CHROMOSOME_CHUNK_KEY, ARBITRARY_CHUNK_KEY])
    def test_missing_output_files_non_strict_messaging(
            self, which_names, extant_files,
            fixed_tempfolder_processor, path_logs_file):

        combination_request_names = self.CHROM_NAMES + self.ARBITRARY_NAMES

        # Do the combine step and get the logged messages.
        num_logs_before_combine = len(loglines(path_logs_file))
        fixed_tempfolder_processor.combine(
                combination_request_names, strict=False)
        logs_from_combine = loglines(path_logs_file)[num_logs_before_combine:]

        # As a control, check that we are in fact over-requesting in combine().
        num_extant_files = len(extant_files)
        num_requested_files = len(combination_request_names)
        assert num_extant_files < num_requested_files

        # The control makes this assertion meaningful.
        num_skips_expected = num_requested_files - num_extant_files
        num_warns_observed = \
                sum(1 for msg in logs_from_combine if "WARN" in msg)
        assert num_skips_expected == num_warns_observed


    @pytest.mark.parametrize(
        argnames=["filetype", "combined_output_type"],
        argvalues=list(itertools.product(["bed", "tsv"], ["bed", "tsv"])),
        ids=lambda (imd_out, end_out):
        " intermediate={} - combined={} ".format(imd_out, end_out))
    @pytest.mark.parametrize(
        argnames="which_names",
        argvalues=[CHROMOSOME_CHUNK_KEY, ARBITRARY_CHUNK_KEY])
    def test_different_format(self, tmpdir, filetype, combined_output_type,
                              which_names, extant_files, num_cores):

        # Manual creation of the processor here to control output type.
        path_output_file = tmpdir.join(
                "testfile.{}".format(combined_output_type)).strpath
        processor = IdentityProcessor(
                PATH_ALIGNED_FILE, cores=num_cores,
                outfile=path_output_file, intermediate_output_type=filetype)
        processor.temp_folder = tmpdir.strpath

        expected_lines = {fp: "file{}: {}\n".format(i, fp)
                          for i, fp in enumerate(extant_files)}
        for fp, line in expected_lines.items():
            with open(fp, 'w') as f:
                f.write(line)

        assert not os.path.exists(path_output_file)

        processor.combine(self.CHUNK_NAMES[which_names], strict=True)
        assert os.path.isfile(path_output_file)
        with open(path_output_file, 'r') as combined:
            observed_lines = combined.readlines()
        assert set(expected_lines.values()) == set(observed_lines)


    @pytest.mark.parametrize(
        argnames="which_names",
        argvalues=[CHROMOSOME_CHUNK_KEY, ARBITRARY_CHUNK_KEY])
    def test_enforces_chunks_limit(
            self, which_names, extant_files, 
            fixed_tempfolder_processor, path_logs_file):
        """ Combination applies only to chunks of interest. """
        extant_read_chunks = self.CHUNK_NAMES[which_names]
        fixed_tempfolder_processor.limit = extant_read_chunks
        bad_chunk_name = "not-a-chunk"
        with pytest.raises(IllegalChunkException) as error:
            fixed_tempfolder_processor.combine(
                    extant_read_chunks + [bad_chunk_name])
        assert bad_chunk_name in error.value.message


    def _names_from_key(self, names_key):
        try:
            return self.CHUNK_NAMES[names_key]
        except KeyError:
            return self.CHROM_NAMES



class IntegrationTests:
    """ A couple of sample end-to-end tests through a simple processor. """
    pass



class FilesystemTests:
    """ Tests regarding interaction between Processor and filesystem """


    @pytest.mark.skip("Implement for context manager use only.")
    def test_removes_tempfolder(self):
        """ Folder for temporary files should be removed. """
        pass


    @pytest.mark.skip("implement for context manager use only.")
    def test_closes_readsfile(self):
        pass



class ArbitraryPartitionTests:
    """ Tests for processor's run() method. """


    @pytest.mark.skip("Not implemented")
    def test_cores_count(self):
        pass


    @pytest.mark.skip("Not implemented")
    def test_chunksize_inference(self):
        pass


    @pytest.mark.skip("Not implemented")
    def test_fixed_chunksize(self):
        pass

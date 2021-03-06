import os
from datetime import date
from pathlib import Path

import re
from typing import Callable

from numpy import matlib

from scripts.MetaSubProcess import MetaSubProcess
from scripts.processes.CreateLonLat import CreateLonLat
from scripts.utils.ArrayUtils import ArrayUtils
from scripts.utils.internal.FolderConstants import FolderConstants

import numpy as np
import math

from scripts.utils.internal.LoggerFactory import LoggerFactory
from scripts.utils.MatlabUtils import MatlabUtils
from scripts.utils.MatrixUtils import MatrixUtils
from scripts.utils.internal.ProcessDataSaver import ProcessDataSaver


class PsFiles(MetaSubProcess):
    """Here we initialize and fill all variables that are needed later.
    Based on StaMPS ps_load_inital_gamma."""

    heading: float = 0.0
    mean_range: float = 0.0
    wavelength: float = 0.0
    mean_incidence: float = 0.0
    master_nr: int = -1  # 'master_ix' in Stamps
    bperp_meaned: np.ndarray
    bperp: np.ndarray  # 'bperp_mat' in Stamps
    ph: np.ndarray
    ll: np.ndarray
    xy: np.ndarray
    da: np.ndarray
    sort_ind: np.matrix  # In Stamps this is la from la1.mat
    master_date: date
    ifgs: np.ndarray  # todo to private variable?
    hgt: np.ndarray
    ifg_dates: list = []  # 'day' in Stamps

    __FILE_NAME = "ps_files"

    def __init__(self, path: str, create_lonlat: CreateLonLat):
        # Parameters that are read from different files and are needed in other processes

        self.__path = Path(path)
        self.__patch_path = Path(path, FolderConstants.PATCH_FOLDER_NAME)

        # Because there are only two parameters to load we can do it here, in constructor
        self.pscands_ij = np.asmatrix(create_lonlat.pscands_ij)
        self.lonlat = np.asmatrix(create_lonlat.lonlat)

        if not self.__path.exists():
            raise FileNotFoundError("No PATCH folder. Load abs.path '{0}'".format(
                str(self.__path.absolute())))
        if not self.__patch_path.exists():
            raise FileNotFoundError(
                "No PATCH folder. Load abs.path '{0}'".format(
                    str(self.__patch_path.absolute())))
        if self.pscands_ij is None:
            raise AttributeError("pscands_ij_array is None")

        self.__logger = self.__logger = LoggerFactory.create("PsFiles")

    def start_process(self):
        self.__logger.info("Start")

        params = self.__load_params_from_rsc_file()

        # In Stamps these where loaded to Matlab params
        self.heading = float(params['heading'])
        self.mean_range = float(params['center_range_slc'])

        self.wavelength = self.__get_wavelength(params)

        self.ifgs = self.__load_ifg_info_from_pscphase()

        self.master_date = self.__get_master_date(params)
        self.master_nr = self.__get_nr_ifgs_less_than_master(self.master_date, self.ifgs)

        self.ifg_dates = self.__get_ifg_dates()

        rg = self.__get_rg(params)

        sat_look_angle = self.__get_look_angle(rg, params)

        self.bperp_meaned, self.bperp = self.__get_bprep(self.ifgs, sat_look_angle, params)

        self.mean_incidence = self.__get_meaned_incidence(rg, params)

        self.ph = self.__get_ph(len(self.ifgs))

        self.ll = self.__get_ll_array()

        self.xy, sort_ind = self.__get_xy()

        self.da = self.__get_da()

        self.hgt = self.__get_hgt()

        self.__sort_results(sort_ind, sat_look_angle)

        self.__logger.info("End")

    def save_results(self, save_path: str):
        ProcessDataSaver(save_path, self.__FILE_NAME).save_data(
            heading=self.heading,
            mean_range=self.mean_range,
            wavelength=self.wavelength,
            mean_incidence=self.mean_incidence,
            master_nr=self.master_nr,
            bprep_meaned=self.bperp_meaned,
            bperp=self.bperp,
            ph=self.ph,
            ll=self.ll,
            xy=self.xy,
            da=self.da,
            sort_ind=self.sort_ind,
            master_date=self.master_date,
            ifgs=self.ifgs,
            hgt=self.hgt,
            ifg_dates=self.ifg_dates)

    def load_results(self, load_path: str):
        file_with_path = os.path.join(load_path, self.__FILE_NAME + ".npz")
        data = np.load(file_with_path)

        self.heading = data['heading']
        self.mean_range = data['mean_range']
        self.wavelength = data['wavelength']
        self.mean_incidence = data['mean_incidence']
        self.master_nr = data['master_nr']
        self.bperp_meaned = data['bprep_meaned']
        self.bperp = data['bperp']
        self.ph = data['ph']
        self.ll = data['ll']
        self.xy = data['xy']
        self.da = data['da']
        self.sort_ind = data['sort_ind']
        self.master_date = data['master_date']
        self.ifgs = data['ifgs']
        self.hgt = data['hgt']
        self.ifg_dates = data['ifg_dates']

    def __get_wavelength(self, params: dict):
        velocity = 299792458  # Speed of signal (m/s)
        freg = float(params['radar_frequency']) * math.pow(10, 9)  # Signal frequency (GHz)
        return velocity / freg

    def __get_bprep(self, ifgs: np.ndarray, sat_look_angle: np.ndarray, params: dict):
        """Here we find bprep_meaned and bprep_arr that were in Stamps accordingly bperp
        and bperp_mat. Saving both of variables just in case.

        In StaMPS ij file where loaded locally and calculations where done with matrix third column.
        In Python this process is too slow (about 30 seconds)."""
        ARRAY_TYPE = np.float64

        cos_sat_look_angle = np.cos(sat_look_angle)
        sin_sat_look_angle = np.sin(sat_look_angle)

        mean_azimuth_line = float(params['azimuth_lines']) / 2 - 0.5

        ij_lon = self.pscands_ij[:, 1]
        nr_ifgs = len(ifgs)
        bperp = matlib.zeros((len(self.pscands_ij), nr_ifgs), dtype=ARRAY_TYPE)

        bc_bn_formula = lambda tcn, baseline_rate: tcn + baseline_rate * (
                ij_lon - mean_azimuth_line) / float(params['prf'])

        for i in range(nr_ifgs):
            tcn, baseline_rate = self.__get_baseline_params(ifgs[i])

            bc = bc_bn_formula(tcn[1], baseline_rate[1])
            bn = bc_bn_formula(tcn[2], baseline_rate[2])
            bprep_line = np.multiply(bc, cos_sat_look_angle) - np.multiply(bn, sin_sat_look_angle)
            bperp[:, i] = bprep_line

        bprep_meaned = np.mean(bperp, 0).transpose()
        # Removing master column (column where are persistent scatterers)
        bperp = MatrixUtils.delete_master_col(bperp, self.master_nr)

        # Return array not matrix
        return ArrayUtils.matrix_to_array(bprep_meaned), ArrayUtils.matrix_to_array(bperp)

    def __get_ph(self, nr_ifgs):
        """pscands.1.ph file load. In this file there are complex binary numbers"""
        BINARY_COMPLEX_TYPE = np.dtype('>c8')  # "big-endian" 64bit complex

        COMPLEX_TYPE = np.complex64

        path_to_ph = self.__patch_path.joinpath("pscands.1.ph")

        with path_to_ph.open("rb") as file:
            imag_array_raw = np.fromfile(file, BINARY_COMPLEX_TYPE)

        imag_mx_len = int(len(imag_array_raw) / nr_ifgs)
        imag_list = []
        count = 0
        for i in range(0, len(imag_array_raw), imag_mx_len):
            matrix_row = imag_array_raw[i:i + imag_mx_len]
            if count == self.master_nr - 1:
                matrix_row = np.ones((imag_mx_len), dtype=COMPLEX_TYPE)
            imag_list.append(matrix_row)

            count += 1

        return np.asarray(imag_list, COMPLEX_TYPE).transpose()

    def __get_meaned_incidence(self, rg: np.ndarray, params: dict):
        sar_to_earth_center_sq = math.pow(float(params['sar_to_earth_center']), 2)
        earth_radius_below_sensor_sq = math.pow(float(params['earth_radius_below_sensor']),
                                                2)

        incidence = np.arccos(
            np.divide(
                (sar_to_earth_center_sq - earth_radius_below_sensor_sq - np.power(rg, 2)),
                (2 * float(params['earth_radius_below_sensor']) * rg)))
        return incidence.mean()

    def __get_baseline_params(self, ifg_name: np.str_):
        """Returns two parameters: tcn (initial baseline) ja baseline_rate.
        These are find in .base files. For every interferogram there is one file.
        There is array sized three each one of these."""

        name_and_ext = ifg_name.split(".")
        base_file_name = name_and_ext[0] + ".base"
        path = Path(base_file_name)

        if path.exists():
            tcn = None
            baseline_rate = None
            with path.open() as basefile:
                for line in basefile:
                    splited = line.split('	')
                    if splited[0] == "initial_baseline(TCN):":
                        tcn = np.array((
                            splited[1], splited[2], splited[3]), dtype=np.float64)
                    elif splited[0] == "initial_baseline_rate:":
                        baseline_rate = np.array((
                            splited[1], splited[2], splited[3]), dtype=np.float64)
                    else:
                        break

            return tcn, baseline_rate
        else:
            raise FileNotFoundError(base_file_name + " not found.")

    def __load_params_from_rsc_file(self) -> dict:
        """From this file we read satellite metadata. Loaded params are put into dict and returned"""

        params = {}

        ALLOWED_PARAMS = ["azimuth_lines",
                          "heading",
                          "range_pixel_spacing",
                          "azimuth_pixel_spacing",
                          "radar_frequency",
                          "prf",
                          "sar_to_earth_center",
                          "earth_radius_below_sensor",
                          "near_range_slc",
                          "center_range_slc",
                          "date"]

        value_regex = re.compile(r"-?[\d*.]+")
        with self.__load_file("rsc.txt", self.__path) as rsc_file:
            rsc_par_file_abs_path = rsc_file.read().strip()

            rsc_par_file = Path(rsc_par_file_abs_path)
            if rsc_par_file.exists():
                with rsc_par_file.open() as rsc_par:
                    for line in rsc_par:
                        # This is last parameter. After that we can stop processing
                        if line == "state_vector_position_1":
                            break

                        splited = line.split(':')
                        key = splited[0]

                        if key in ALLOWED_PARAMS:
                            if key == 'date':
                                # If not seprated with coma or is not string then removes spaces
                                value = re.sub("[\t\n\v]", "", splited[1])
                            else:
                                value = value_regex.findall(splited[1])[0]

                            params[key] = value
            else:
                raise FileNotFoundError(
                    "No file. Abs.path '" + str(rsc_par_file.absolute()) + "'")

            return params

    def __get_master_date(self, params: dict):
        """'date' is from load_params_from_rsc_file and is master date. We split that string here
        and make it datetime"""
        date_arr = params["date"].split('  ')
        return date(int(date_arr[0]), int(date_arr[1]), int(date_arr[2]))

    def __load_file(self, name: str, path: Path):
        file_path = Path(path, name)

        if file_path.exists():
            return file_path.open()
        else:
            raise FileNotFoundError("No file " + name + ". Abs.path " + str(file_path.absolute()))

    def __load_ifg_info_from_pscphase(self):
        """In pscphase.in file there are paths to inteferograms. Those are returned in this
        function. In filename there is master date and inteferogram date."""

        path = self.__path.joinpath("pscphase.in")
        if path.exists():
            pscphase = np.genfromtxt(str(path), dtype=str, skip_header=True)
            return pscphase
        else:
            raise FileNotFoundError("pscphase.in not found. AbsPath {0}".format(
                str(path.absolute())))

    def __get_nr_ifgs_less_than_master(self, master_date: date, ifgs: np.ndarray):
        """How many inteferograms there are before master"""
        comp_fun = lambda x, y: x > y
        return self.get_nr_ifgs_copared_to_master(comp_fun, ifgs, master_date)

    def __get_ll_array(self):
        return (MatlabUtils.max(self.lonlat) + MatlabUtils.min(self.lonlat)) / 2

    def __get_xy(self):

        """ij array is taken last two columns that are x an y.
        This is multiplied with scalar, fixes data by rotating image and later sorted by y column.
        Here we additionally also add sorting index column that other arrays can use."""
        xy = np.fliplr(self.pscands_ij.copy())[:, 0:2]
        xy[:, 0] *= 20
        xy[:, 1] *= 4

        xy = self.__scene_rotate(xy)

        # Return value is array. Make it correct here
        xy = ArrayUtils.matrix_to_array(xy)

        sort_ind = np.lexsort((xy[:, 0], xy[:, 1]))
        sorted_xy = xy[sort_ind]

        # TODO Multiply to closest millimeter. But why it is already int
        sorted_xy = np.around(sorted_xy * 1000) / 1000

        return sorted_xy, sort_ind

    def __scene_rotate(self, xy: np.matrix):
        # TODO find better name for this variable
        theta = (180 - self.heading) * math.pi / 180
        if theta > math.pi:
            theta -= 2 * math.pi

        rotm = np.array([[math.cos(theta), math.sin(theta)], [-math.sin(theta), math.cos(theta)]])
        xy = xy.H

        rotated_xy = rotm * xy
        # We need maximum element, that's why we don't use axis parameter
        is_improved = np.amax(rotated_xy[0]) - np.amin(rotated_xy[0]) < np.amax(xy[0]) - np.amin(
            xy[0]) and np.amax(rotated_xy[1]) - np.amin(rotated_xy[1]) < np.amax(xy[1]) - np.amin(
            xy[1])
        if is_improved:
            xy = rotated_xy

        xy = xy.H
        return xy

    def __sort_results(self, sort_ind: np.ndarray, sat_look_angle: np.ndarray):
        self.ph = self.ph[sort_ind]
        self.bperp = self.bperp[sort_ind]
        self.da = self.da[sort_ind]

        self.pscands_ij = MatrixUtils.sort_matrix_with_sort_array(self.pscands_ij, sort_ind)
        self.lonlat = MatrixUtils.sort_matrix_with_sort_array(self.lonlat, sort_ind)

        self.sort_ind = sat_look_angle[sort_ind]
        self.hgt = self.hgt[sort_ind]

    def __get_da(self):
        # Because the file is small (only one column) then loadtxt function is quick enough
        return np.loadtxt(str(Path(self.__patch_path, "pscands.1.da")))

    def __get_look_angle(self, rg: np.ndarray, params):
        sar_to_earth_center_sq = math.pow(float(params['sar_to_earth_center']), 2)
        earth_radius_below_sensor_sq = math.pow(float(params['earth_radius_below_sensor']),
                                                2)
        return np.arccos(np.divide(
            sar_to_earth_center_sq + np.power(rg, 2) - earth_radius_below_sensor_sq,
            2 * float(params['sar_to_earth_center']) * rg))

    def __get_hgt(self):
        FLOAT_TYPE = ">f4"  # "big-endian" float32
        path_to_hgt = self.__patch_path.joinpath("pscands.1.hgt")

        with path_to_hgt.open("rb") as file:
            hgt_raw = np.fromfile(file, FLOAT_TYPE)

        hgt = hgt_raw.conj().transpose()

        return hgt

    def __get_rg(self, params: dict):
        ij_lat = self.pscands_ij[:, 2]
        return float(params['near_range_slc']) + ij_lat * float(params['range_pixel_spacing'])

    def __get_ifg_dates(self) -> []:
        ifgs = self.ifgs

        ifg_dates = []
        for ifg_path in ifgs:
            ifg_date_str_yyyymmdd = ifg_path[-13:-5]
            ifg_datetime = date(int(ifg_date_str_yyyymmdd[:4]),
                                int(ifg_date_str_yyyymmdd[4:6]),
                                int(ifg_date_str_yyyymmdd[6:8]))

            ifg_dates.append(ifg_datetime)

        return ifg_dates

    def get_ps_variables(self):
        """For exporting variables that are used in PsEstGamma and PsSelect"""

        nr_ifgs = len(self.ifgs)
        nr_ps = len(self.pscands_ij)

        return self.ph, self.bperp, nr_ifgs, nr_ps, self.xy, self.da

    def get_nr_ifgs_copared_to_master(self, comp_fun: Callable[[date, date], bool],
                                      ifgs=np.array([]), master_date: date=None):
        """
        How many images there are after or before master image plus one. Dates are parsed are from
        filename.

        :param comp_fun: Comparison function. Example: x > y (two params, returns boolean)
        :param ifgs: File paths that are used to get date
        :param master_date: Master date (optional). If not set then self.master_date is used.
        :return: integer, number of images
        """

        if ifgs is None or len(ifgs) == 0:
            ifgs = self.ifgs

        if master_date is None:
            master_date = self.master_date

        result = 1  # In StaMPS they added one after this processing, in return
        for ifg_path in ifgs:
            ifg_date_str_yyyymmdd = ifg_path[-13:-5]
            ifg_datetime = date(int(ifg_date_str_yyyymmdd[:4]),
                                int(ifg_date_str_yyyymmdd[4:6]),
                                int(ifg_date_str_yyyymmdd[6:8]))

            if comp_fun(ifg_datetime, master_date):
                result += 1
        return result

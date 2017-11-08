from datetime import datetime
import numpy as np
import numpy.matlib
import sys

from scripts.MetaSubProcess import MetaSubProcess
from scripts.processes.PsEstGamma import PsEstGamma
from scripts.processes.PsFiles import PsFiles
from scripts.processes.PsSelect import PsSelect
from scripts.utils.ArrayUtils import ArrayUtils
from scripts.utils.LoggerFactory import LoggerFactory
from scripts.utils.MatlabUtils import MatlabUtils


class PsWeed(MetaSubProcess):
    """Pikslite filtreerimine teiste naabrusest. Valitakse hulgast vaid selgemad"""

    IND_ARRAY_TYPE = np.int32

    def __init__(self, ps_files: PsFiles, ps_est_gamma: PsEstGamma, ps_select: PsSelect):
        self.ps_files = ps_files
        self.ps_select = ps_select
        self.ps_est_gamma = ps_est_gamma

        self.__logger = LoggerFactory.create("PsWeed")

        self.__time_win = 730
        self.__weed_standard_dev = 1
        self.__weed_max_noise = sys.maxsize  # Stampsis oli tavaväärtus inf
        self.__weed_zero_elevation = False
        self.__weed_neighbours = True
        # todo drop_ifg_index on juba PsSelect'is
        self.__drop_ifg_index = np.array([])
        self.__small_baseline = True

    class __DataDTO(object):

        def __init__(self, ind: np.ndarray, ph_res: np.ndarray, coh_thresh_ind: np.ndarray,
                     k_ps: np.ndarray, c_ps: np.ndarray, coh_ps: np.ndarray, pscands_ij: np.matrix,
                     xy: np.ndarray, lonlat: np.matrix, hgt: np.ndarray, ph: np.ndarray,
                     ph2: np.ndarray, ph_patch_org: np.ndarray, bperp: np.ndarray, nr_ifgs: int,
                     nr_ps: int, master_date: datetime):
            self.ind = ind
            self.ph_res = ph_res
            self.coh_thresh_ind = coh_thresh_ind
            self.k_ps = k_ps
            self.c_ps = c_ps
            self.coh_ps = coh_ps
            self.pscands_ij = pscands_ij
            self.xy = xy
            self.lonlat = lonlat
            self.hgt = hgt
            self.ph_patch_org = ph_patch_org
            self.ph = ph
            self.ph2 = ph2
            self.bperp = bperp
            self.nr_ifgs = nr_ifgs
            self.nr_ps = nr_ps
            self.master_date = master_date

    def start_process(self):
        self.__logger.info("Start")

        data = self.__load_ps_params()
        # Stamps*is oli see nimetatud kui nr_ps, aga see on meil juba olemas
        coh_thresh_ind_len = len(data.coh_thresh_ind)
        self.__logger.debug("Loaded data. coh_thresh_ind.len: {0}, coh_thresh_ind_len: {1}"
                            .format(coh_thresh_ind_len, data.nr_ps))

        ij_shift = self.__get_ij_shift(data.pscands_ij, coh_thresh_ind_len)
        self.__logger.debug("ij_shift.len: {0}".format(len(ij_shift)))

        neighbour_ind = self.__init_neighbours(ij_shift, coh_thresh_ind_len)
        self.__logger.debug("neighbours.len: {0}".format(len(neighbour_ind)))

        neighbour_ps = self.__find_neighbours(ij_shift, coh_thresh_ind_len, neighbour_ind)
        self.__logger.debug("neighbour_ps.len: {0}".format(len(neighbour_ps)))

        self.__select_best(neighbour_ps, coh_thresh_ind_len, data.coh_ps)

        self.__logger.info("End")

    def __load_ps_params(self):

        def get_from_ps_select():
            ind = self.ps_select.keep_ind
            ph_res = self.ps_select.ph_res[ind]

            if len(ind) > 0:
                coh_thresh_ind = self.ps_select.coh_thresh_ind[ind]
                c_ps = self.ps_select.c_ps[ind]
                k_ps = self.ps_select.k_ps[ind]
                coh_ps = self.ps_select.coh_ps2[ind]
            else:
                coh_thresh_ind = self.ps_select.coh_thresh_ind
                c_ps = self.ps_select.c_ps
                k_ps = self.ps_select.k_ps
                coh_ps = self.ps_select.coh_ps2

            return ind, ph_res, coh_thresh_ind, k_ps, c_ps, coh_ps

        def get_from_ps_files():
            pscands_ij = self.ps_files.pscands_ij[coh_thresh_ind]
            xy = self.ps_files.xy[coh_thresh_ind]
            ph = self.ps_files.ph[coh_thresh_ind]
            lonlat = self.ps_files.lonlat[coh_thresh_ind]
            hgt = self.ps_files.hgt[coh_thresh_ind]

            return pscands_ij, xy, ph, lonlat, hgt

        def get_from_ps_est_gamma():

            ph_patch_org = self.ps_est_gamma.ph_patch[coh_thresh_ind, :]
            ph, bperp, nr_ifgs, nr_ps, _, _ = self.ps_files.get_ps_variables()
            master_date = self.ps_files.master_date

            return ph_patch_org, ph, bperp, nr_ifgs, nr_ps, master_date

        # fixme ph_path'e on Stampsis ainult üks.

        ind, ph_res, coh_thresh_ind, k_ps, c_ps, coh_ps = get_from_ps_select()

        pscands_ij, xy, ph2, lonlat, hgt = get_from_ps_files()

        ph_patch_org, ph, bperp, nr_ifgs, nr_ps, master_date = get_from_ps_est_gamma()

        # Stamps'is oli siin oli ka lisaks 'all_da_flag' ja leiti teised väärtused muutujatele k_ps,
        # c_ps, coh_ps, ph_patch_org, ph_res

        return self.__DataDTO(ind, ph_res, coh_thresh_ind, k_ps, c_ps, coh_ps, pscands_ij, xy,
                              lonlat, hgt, ph, ph2, ph_patch_org, bperp, nr_ifgs, nr_ps,
                              master_date)

    def __get_ij_shift(self, pscands_ij: np.matrix, coh_ps_len: int) -> np.ndarray:
        ij = np.asarray(pscands_ij[:, 1:3])
        repmated = np.matlib.repmat(np.array([2, 2]) - ij.min(axis=0), coh_ps_len, 1)
        ij_shift = ij + repmated

        return ij_shift

    def __init_neighbours(self, ij_shift: np.ndarray, coh_ps_len: int) -> np.ndarray:

        def arange_neighbours_select_arr(i, ind):
            return ArrayUtils.arange_include_last(ij_shift[i, ind] - 2, ij_shift[i, ind])

        neighbour_ind = np.zeros((MatlabUtils.max(ij_shift[:, 0]) + 1,
                                  MatlabUtils.max(ij_shift[:, 1]) + 1), self.IND_ARRAY_TYPE)
        for i in range(coh_ps_len):
            start = arange_neighbours_select_arr(i, 0)
            end = arange_neighbours_select_arr(i, 1)

            # Selleks, et saada len(start) * len(end) massiivi tuleb numpy's sedasi selekteerida
            # Võib kasutada ka neighbour_ind[start, :][:, end], aga see ei luba pärast sama moodi
            # väärtustada
            neighbours_val = neighbour_ind[np.ix_(start, end)]
            neighbours_val[neighbours_val == 0] = i + 1
            neighbours_val[1, 1] = 0  # Keskmise väärtustamine

            neighbour_ind[np.ix_(start, end)] = neighbours_val

        return neighbour_ind

    def __find_neighbours(self, ij_shift: np.ndarray, coh_thresh_ind_len: int,
                          neighbour_ind: np.ndarray) -> np.ndarray:
        # Loome tühja listi, kus on sees tühjad numpy massivid
        neighbour_ps = [np.array([], self.IND_ARRAY_TYPE)] * (coh_thresh_ind_len + 1)
        for i in range(coh_thresh_ind_len):
            neighbour_val = neighbour_ind[ij_shift[i, 0], ij_shift[i, 1]]
            if neighbour_val > 0:
                neighbour_ps[neighbour_val] = np.append(neighbour_ps[neighbour_val], [i])

        return np.array(neighbour_ps)

    def __select_best(self, neighbour_ps: np.ndarray, coh_thresh_ind_len: int,
                      coh_ps: np.ndarray) -> np.ndarray:
        weed_ind = np.ones((coh_thresh_ind_len, 1), dtype=bool)

        for i in range(coh_thresh_ind_len):
            # todo Stamps'is oli isEmpty kontroll selle asemel
            same_ps = neighbour_ps[i]
            if len(same_ps) != 0:
                i2 = 0
                while i2 <= len(same_ps):
                    ps_i = same_ps[i2]
                    same_ps = np.array([same_ps, neighbour_ps[ps_i]])
                    neighbour_ps[ps_i] = np.array([])
                    i2 += 1

                same_ps = np.unique(same_ps)

                highest_coh = MatlabUtils.max(coh_ps[same_ps])

                low_coh_ind = np.ones(same_ps.shape, dtype=bool)
                low_coh_ind[highest_coh] = False

                same_ps = same_ps[low_coh_ind]
                weed_ind[same_ps] = False

        return weed_ind

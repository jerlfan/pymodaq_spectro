import sys
import tables
from collections import OrderedDict
import datetime
import os
import numpy as np
from copy import deepcopy
from PyQt5 import QtGui, QtWidgets
from PyQt5.QtCore import QObject, pyqtSlot, pyqtSignal, QLocale, QDateTime, QRectF, QDate, QThread, Qt

from pyqtgraph.dockarea import Dock
from pymodaq.daq_utils.daq_utils import DockArea

from pyqtgraph.parametertree import Parameter, ParameterTree
import pyqtgraph.parametertree.parameterTypes as pTypes
import pymodaq.daq_utils.custom_parameter_tree as custom_tree
from pymodaq.daq_utils.daq_utils import select_file, Enm2cmrel, Ecmrel2Enm, nm2eV, eV2nm, eV2radfs, l2w, getLineInfo, ThreadCommand
from pymodaq.daq_utils.plotting.qled import QLED

from pymodaq.daq_viewer.daq_viewer_main import DAQ_Viewer
from pymodaq.daq_move.daq_move_main import DAQ_Move
from pymodaq.daq_utils.plotting.viewer0D.viewer0D_main import Viewer0D
from pymodaq.daq_utils.plotting.viewer1D.viewer1D_main import Viewer1D
from pymodaq.daq_utils.plotting.viewer2D.viewer2D_main import Viewer2D
from pymodaq.daq_utils.h5browser import browse_data
from pymodaq.daq_utils import daq_utils as utils
from pymodaq.daq_utils.h5browser import H5Browser

from pymodaq_spectro.utils.calibration import Calibration

class Spectrometer(QObject):
    """
    Defines a Spectrometer object, unified interface for many spectrometers

    Parameters that could be set in the selected detector plugin (should be defined there):
    'laser_wl' : value of the configured laser (could eventually be changed, case of Xplora, Labram...)
    'spectro_center_freq': value of the configured grating center wavelength (could eventually be changed, case of Shamrock, Xplora...)


    """
    #custom signal that will be fired sometimes. Could be connected to an external object method or an internal method
    log_signal = pyqtSignal(str)

    #list of dicts enabling the settings tree on the user interface
    params = [{'title': 'Configuration settings:', 'name': 'config_settings', 'type': 'group', 'children': [
                        {'title': 'Laser wavelength (nm):', 'name': 'laser_wl', 'type': 'float', 'value': 515.},
                        {'title': 'Laser wavelength (nm):', 'name': 'laser_wl_list', 'type': 'list', 'values':['']},
                        {'title': 'List of detectors:', 'name': 'list_detector', 'type': 'list', 'values':['']},
                        {'title': 'Show detector:', 'name': 'show_det', 'type': 'bool', 'value': False},
                        ],},
              {'title': 'Calibration settings:', 'name': 'calib_settings', 'type': 'group', 'children': [
                  {'title': 'Use calibration:', 'name': 'use_calib', 'type': 'bool', 'value': False},
                  {'title': 'Calibration coeffs:', 'name': 'calib_coeffs', 'type': 'group', 'children': [
                      {'title': 'Center wavelength (nm):', 'name': 'center_calib', 'type': 'float', 'value': 515.},
                      {'title': 'Slope (nm/pxl):', 'name': 'slope_calib', 'type': 'float', 'value': 1.},
                      {'title': 'Second order :', 'name': 'second_calib', 'type': 'float', 'value': 0},
                      {'title': 'third:', 'name': 'third_calib', 'type': 'float', 'value': 0},]},
                  {'title': 'Perform calibration:', 'name': 'do_calib', 'type': 'bool', 'value': False},

                     ]},
              {'title': 'Acquisition settings:', 'name': 'acq_settings', 'type': 'group', 'children': [
                  {'title': 'Spectro. Center:', 'name': 'spectro_center_freq', 'type': 'float', 'value': 800,},
                  {'title': 'Spectro. Center:', 'name': 'spectro_center_freq_txt', 'type': 'str', 'value': '????', 'readonly':True },
                  {'title': 'Units:', 'name': 'units', 'type': 'list', 'value': 'nm', 'values': ['nm', 'cm-1', 'eV']},
                  {'title': 'Exposure (ms):', 'name': 'exposure_ms', 'type': 'float', 'value': 100, },
              ]},
              ]


    def __init__(self, parent):
        QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))
        super().__init__()
        if not isinstance(parent, DockArea):
            raise Exception('no valid parent container, expected a DockArea')

        self.wait_time = 2000 #ms

        self.dockarea = parent
        self.mainwindow = parent.parent()
        self.spectro_widget = QtWidgets.QWidget()

        """
        List of the possible plugins that could be used with Spectrometer module
        type : dimensionality of the detector
        name: name of the plugin
        calib = True means there is a builtin calibration of the frequency axis
        movable : tells if the dispersion can be set (for instance by moving a grating)
        unit: valid only if calib is True. Unit of the calibration axis (x_axis of the detector), most often in
              nanometers. Possible values are 'nm', 'radfs' (rad/femtosecond), 'eV'
        laser: if False,  laser cannot be changed by the program, do it manually
        laser_list: if laser is True, laser_list gives a list of selectable lasers
        
        """
        self.detectors = [ dict(type='DAQ1D', name='Mock', plugin_name='Mock', calib=False, movable=False, unit='nm', laser=False, laser_list=[]),
                           dict(type='DAQ1D', name='Mock_spectro', plugin_name='Mock_spectro', calib=True, movable=True, unit='nm',
                                laser=True, laser_list=[405, 515, 632.8]),
                            dict(type='DAQ1D', name='OceanOptics', plugin_name='OceanOptics', calib=True, movable=False, unit='nm', laser=False, laser_list=[]),
                            dict(type='DAQ1D', name='Shamrock', plugin_name='Shamrock', calib=True, movable=True, unit='nm', laser=False, laser_list=[]),
                            dict(type='DAQ1D', name='TCPServer', plugin_name='TCPServer', calib=False, movable=False, unit=None, laser=False, laser_list=[]),
                            dict(type='DAQ2D', name='SpectroUV', plugin_name='AndorCCD', calib=False, movable=False, unit= None, laser=False, laser_list=[]),]
        self.current_det = None #will be after initialization one of these dictionaries

        self.laser_set_manual = True

        #init the object parameters
        self.detector = None
        self.save_file_pathname = None
        self._spectro_wl = 550 # center wavelngth of the spectrum
        self.viewer_freq_axis = dict(data=None, label='Photon energy', units='')
        self.raw_data = []

        #init the user interface
        self.set_GUI()

        dets = [det['name'] for det in self.detectors]
        self.settings.child('config_settings', 'list_detector').setOpts(limits=dets)
        self.settings.child('config_settings', 'list_detector').setValue('Mock')
        self.show_detector(False)



    def set_GUI(self):
        ###########################################
        ###########################################
        #init the docks containing the main widgets

        #######################################################################################################################
        #create a dock containing a viewer object, displaying the data for the spectrometer
        self.dock_viewer = Dock('Viewer dock', size=(350, 350))
        self.dockarea.addDock(self.dock_viewer, 'left')
        target_widget = QtWidgets.QWidget()
        self.viewer = Viewer1D(target_widget)
        self.dock_viewer.addWidget(target_widget)

        ###################################################################################
        #create 2 docks to display the DAQ_Viewer (one for its settings, one for its viewer)
        dock_detector_settings = Dock("Detector Settings", size=(350, 350))
        self.dockarea.addDock(dock_detector_settings, 'right')
        dock_detector_settings.float()
        self.detector_area = dock_detector_settings.area
        dock_detector = Dock("Detector Viewer", size=(350, 350))
        self.detector_area.addDock(dock_detector, 'right', dock_detector_settings)
        #init one daq_viewer object named detector
        self.detector = DAQ_Viewer(self.dockarea, dock_settings=dock_detector_settings,
                                                 dock_viewer=dock_detector, title="Spectro Detector", DAQ_type='DAQ1D')
        self.detector.log_signal.connect(self.update_status)
        self.detector.init_signal[bool].connect(self.initialized)
        self.detector.custom_sig[ThreadCommand].connect(self.cmd_from_det)
        ################################################################
        #create a logger dock where to store info senf from the programm
        self.dock_logger = Dock("Logger")
        self.logger_list = QtWidgets.QListWidget()
        self.logger_list.setMinimumWidth(300)
        self.dock_logger.addWidget(self.logger_list)
        self.dockarea.addDock(self.dock_logger, 'right')
        self.log_signal[str].connect(self.add_log)



        ############################################
        # creating a menubar
        self.menubar = self.mainwindow.menuBar()
        self.create_menu(self.menubar)

        #creating a toolbar
        self.toolbar = QtWidgets.QToolBar()
        self.create_toolbar()
        self.mainwindow.addToolBar(self.toolbar)

        #creating a status bar
        self.statusbar = QtWidgets.QStatusBar()
        self.statusbar.setMaximumHeight(25)

        self.status_laser =  QtWidgets.QLabel('????')
        self.status_laser.setAlignment(Qt.AlignCenter)
        #self.status_laser.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        #self.status_laser.setReadOnly(True)
        self.status_laser.setMaximumWidth(80)
        self.status_laser.setMinimumWidth(80)
        self.status_laser.setToolTip('Current laser wavelength')
        self.status_laser.setStyleSheet("background-color: red")

        self.status_center = QtWidgets.QLabel('????')
        self.status_center.setAlignment(Qt.AlignCenter)
        #self.status_center.setReadOnly(True)
        #self.status_center.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.status_center.setMaximumWidth(80)
        self.status_center.setMinimumWidth(80)
        self.status_center.setToolTip('center frequency of the spectrum, either in nm or cm-1')
        self.status_center.setStyleSheet("background-color: red")

        self.status_init = QLED()
        self.status_init.setToolTip('Initialization state of the detector')
        self.status_init.set_as_false()
        self.status_init.clickable = False

        self.statusbar.addPermanentWidget(self.status_laser)
        self.statusbar.addPermanentWidget(self.status_center)
        self.statusbar.addPermanentWidget(self.status_init)
        self.dockarea.window().setStatusBar(self.statusbar)


        #############################################
        self.settings = Parameter.create(name='settings', type='group', children=self.params)
        self.settings.sigTreeStateChanged.connect(self.parameter_tree_changed)

        dock_config_settings = Dock('Configuration', size=(300, 350))
        self.dockarea.addDock(dock_config_settings, 'above', self.dock_logger)
        # create main parameter tree
        self.config_settings_tree = ParameterTree()
        dock_config_settings.addWidget(self.config_settings_tree, 10)
        self.config_settings_tree.setMinimumWidth(300)
        self.config_settings_tree.setParameters(self.settings.child(('config_settings')), showTop=False)
        #any change to the tree on the user interface will call the parameter_tree_changed method where all actions will be applied

        dock_calib_settings = Dock('Calibration', size=(300, 350))
        self.dockarea.addDock(dock_calib_settings, 'above', self.dock_logger)
        # create main parameter tree
        self.calib_settings_tree = ParameterTree()
        dock_calib_settings.addWidget(self.calib_settings_tree, 10)
        self.calib_settings_tree.setMinimumWidth(300)
        self.calib_settings_tree.setParameters(self.settings.child(('calib_settings')), showTop=False)
        #any change to the tree on the user interface will call the parameter_tree_changed method where all actions will be applied



        #this one for the custom application settings
        dock_acq_settings = Dock('Acquisition', size=(300, 350))
        self.dockarea.addDock(dock_acq_settings, 'above', dock_config_settings)
        # create main parameter tree
        self.acq_settings_tree = ParameterTree()
        dock_acq_settings.addWidget(self.acq_settings_tree, 10)
        self.acq_settings_tree.setMinimumWidth(300)
        self.acq_settings_tree.setParameters(self.settings.child(('acq_settings')), showTop=False)


    @pyqtSlot(ThreadCommand)
    def cmd_from_det(self,status):
        try:
            if status.command == 'spectro_wl':
                self.status_center.setStyleSheet("background-color: green")
                self.spectro_wl_is(status.attributes[0])

            elif status.command == 'laser_wl':
                #self.laser_set_manual = False
                self.settings.child('config_settings', 'laser_wl_list').setValue(status.attributes[0])
                self.status_laser.setText('{:}nm'.format(status.attributes[0]))
                self.status_laser.setStyleSheet("background-color: green")
                self.update_center_frequency(self.spectro_wl)


            elif status.command == 'exposure_ms':
                self.settings.child('acq_settings', 'exposure_ms').setValue(status.attributes[0])

            elif status.command == "x_axis":
                x_axis = status.attributes[0]
                if np.any(x_axis['data'] != self.viewer_freq_axis['data']) and self.current_det['calib']:
                    self.viewer_freq_axis.update(x_axis)
                    self.update_axis()

        except Exception as e:
            self.update_status(getLineInfo()+ str(e),self.wait_time,'log')

    def update_status(self,txt, wait_time=1000, log_type=None):
        """

        """
        self.statusbar.showMessage(txt,wait_time)
        if log_type is not None:
            self.log_signal.emit(txt)


    def init_detector(self):
        self.settings.child('config_settings', 'list_detector').setOpts(readonly=self.init_action.isChecked())
        if self.init_action.isChecked():
            self.statusbar.showMessage('Initializing')

            self.detector.ui.IniDet_pb.click()
            if self.detector.ui.Detector_type_combo.currentText() == 'Mock':
                self.detector.settings.child('main_settings', 'wait_time').setValue(100)
            QtWidgets.QApplication.processEvents()
            QThread.msleep(1000)
            self.detector.grab_done_signal.connect(self.show_data)
            self.statusbar.clearMessage()

            #set current detector
            det_index = [det['plugin_name'] for det in self.detectors].index(self.detector.ui.Detector_type_combo.currentText())
            self.current_det = self.detectors[det_index]

            self.settings.sigTreeStateChanged.disconnect(self.parameter_tree_changed)
            if self.current_det['laser']:
                self.settings.child('config_settings', 'laser_wl_list').show()
                self.settings.child('config_settings', 'laser_wl').hide()
                self.settings.child('config_settings', 'laser_wl_list').setOpts(limits=self.current_det['laser_list'])
            else:
                self.settings.child('config_settings', 'laser_wl').show()
                self.settings.child('config_settings', 'laser_wl_list').hide()
            self.settings.sigTreeStateChanged.connect(self.parameter_tree_changed)

            #apply current detector particularities
            #self.settings.child('acq_settings', 'spectro_center_freq').setOpts(readonly=not self.current_det['movable'])
            self.get_spectro_wl()
            QtWidgets.QApplication.processEvents()


            self.get_laser_wl()
            QtWidgets.QApplication.processEvents()
        else:
            self.detector.ui.IniDet_pb.click()
            self.status_laser.setText('????')
            self.status_center.setText('????')
            self.status_laser.setStyleSheet("background-color: red")
            self.status_center.setStyleSheet("background-color: red")
            #self.detector.grab_done_signal.disconnect()

    @pyqtSlot(bool)
    def initialized(self, state):
        self.grab_action.setEnabled(state)
        self.snap_action.setEnabled(state)
        if state:
            self.status_init.set_as_true()

        else:
            self.status_init.set_as_false()

    def update_center_frequency(self, spectro_wl):
        self._spectro_wl = spectro_wl
        if self.settings.child('acq_settings', 'units').value() == 'nm':
            self.settings.child('acq_settings', 'spectro_center_freq').setValue(spectro_wl)
        elif self.settings.child('acq_settings', 'units').value() == 'cm-1':
            self.settings.child('acq_settings', 'spectro_center_freq').setValue(Enm2cmrel(spectro_wl,
                                                                                          self.settings.child(
                                                                                              'config_settings',
                                                                                              'laser_wl').value()))
        elif self.settings.child('acq_settings', 'units').value() == 'eV':
            self.settings.child('acq_settings', 'spectro_center_freq').setValue(nm2eV(spectro_wl))

        self.status_center.setText('{:} {:}'.format(self.settings.child('acq_settings', 'spectro_center_freq').value(),
                                                    self.settings.child('acq_settings', 'units').value()))



    def spectro_wl_is(self, spectro_wl):
        """
        this slot receives a signal from the detector telling it what's the current spectro_wl
        Parameters
        ----------
        spectro_wl
        """
        self._spectro_wl = spectro_wl
        self.update_center_frequency(spectro_wl)


    def set_spectro_wl(self, spectro_wl):
        if self.current_det['movable']:
            self.detector.command_detector.emit(ThreadCommand('set_spectro_wl', [spectro_wl]))

    def get_spectro_wl(self):
        if self.current_det['calib']:
            self.settings.child('acq_settings', 'spectro_center_freq').show()
            self.settings.child('acq_settings', 'spectro_center_freq_txt').hide()
            self.detector.command_detector.emit(ThreadCommand('get_spectro_wl'))
            self.detector.command_detector.emit(ThreadCommand('get_axis'))
        else:
            self.settings.child('acq_settings', 'spectro_center_freq').hide()
            self.settings.child('acq_settings', 'spectro_center_freq_txt').show()
            self.viewer_freq_axis['unit'] = 'Pxls'

    def get_laser_wl(self):
        if self.current_det['laser']:
            self.detector.command_detector.emit(ThreadCommand('get_laser_wl'))
        else:
            self.settings.child('config_settings', 'laser_wl').setValue(0)
    @property
    def spectro_wl(self):
        # try to get the param value from detector (if it has been added in the plugin)
        return self._spectro_wl

    @spectro_wl.setter
    def spectro_wl(self, spec_wl):
        # try to get the param value from detector (if it has been added in the plugin)
        self.set_spectro_wl(spec_wl)


    def show_detector(self, show=True):
        self.detector_area.window().setVisible(show)


    def parameter_tree_changed(self, param, changes):
        for param, change, data in changes:
            path = self.settings.childPath(param)
            if path is not None:
                childName = '.'.join(path)
            else:
                childName = param.name()
            if change == 'childAdded':
                pass

            elif change == 'value':
                if param.name() == 'list_detector':
                    if data is not None:
                        dets = [det['name'] for det in self.detectors]
                        ind = dets.index(data)
                        det_type = self.detectors[ind]['type']
                        det_plugin = self.detectors[ind]['plugin_name']
                        self.detector.ui.DAQ_type_combo.setCurrentText(det_type)
                        QtWidgets.QApplication.processEvents()

                        self.detector.ui.Detector_type_combo.setCurrentText(det_plugin)

                elif param.name() == 'show_det':
                    self.show_detector(data)

                elif param.name() == 'spectro_center_freq':
                    unit = self.settings.child('acq_settings', 'units').value()
                    if unit == 'nm':
                        center_wavelength = data
                    elif unit == 'cm-1':
                        center_wavelength = Ecmrel2Enm(data, self.settings.child( 'config_settings', 'laser_wl').value())
                    elif unit == 'eV':
                        center_wavelength = eV2nm(data)

                    if int(self.spectro_wl*100) != int(100*center_wavelength): #comprison at 1e-2
                        self.spectro_wl = center_wavelength

                    self.update_axis()



                elif param.name() == 'units':
                    if data == 'nm':
                        self.settings.child('acq_settings', 'spectro_center_freq').setValue(self._spectro_wl)
                    elif data == 'cm-1':
                        self.settings.child('acq_settings', 'spectro_center_freq').setValue(Enm2cmrel(self._spectro_wl,
                                                self.settings.child( 'config_settings', 'laser_wl').value()))
                    elif data == 'eV':
                        self.settings.child('acq_settings', 'spectro_center_freq').setValue(nm2eV(self._spectro_wl))

                    self.status_center.setText(
                        '{:} {:}'.format(self.settings.child('acq_settings', 'spectro_center_freq').value(),
                                         self.settings.child('acq_settings', 'units').value()))

                elif param.name() == 'laser_wl_list':
                    if data is not None:
                        self.move_laser_wavelength(data)

                elif param.name() == 'laser_wl':
                    if data is not None:
                        self.move_laser_wavelength(data)
                        if int(data) == 0:
                            self.settings.child('acq_settings', 'units').setValue('nm')
                            self.settings.child('acq_settings', 'units').setOpts(readonly=True)
                        else:
                            self.settings.child('acq_settings', 'units').setOpts(readonly=False)
                        if data != 0:
                            self.set_manual_laser_wl(data)



                elif param.name() == 'exposure_ms':
                    self.detector.command_detector.emit(ThreadCommand('set_exposure_ms', [data]))

                elif param.name() == 'do_calib':
                    dock = Dock('Calibration module')
                    self.dockarea.addDock(dock)
                    self.calibration = Calibration(self.dockarea)
                    dock.addWidget(self.calibration)

                    self.calibration.coeffs_calib.connect(self.update_calibration)

                elif param.name() in custom_tree.iter_children(self.settings.child('calib_settings', 'calib_coeffs')) or param.name() == 'use_calib':
                    if self.settings.child('calib_settings', 'use_calib').value():
                        calib_coeffs = [self.settings.child('calib_settings', 'calib_coeffs', 'third_calib').value(),
                                        self.settings.child('calib_settings', 'calib_coeffs', 'second_calib').value(),
                                        self.settings.child('calib_settings', 'calib_coeffs', 'slope_calib').value(),
                                        self.settings.child('calib_settings', 'calib_coeffs', 'center_calib').value()]

                        self.update_center_frequency(self.settings.child('calib_settings', 'calib_coeffs', 'center_calib').value())
                        self.settings.child('acq_settings', 'spectro_center_freq').show()
                        self.settings.child('acq_settings', 'spectro_center_freq').setOpts(readonly=True)
                        self.status_center.setStyleSheet("background-color: green")
                        self.settings.child('acq_settings', 'spectro_center_freq_txt').hide()
                        x_axis_pxls = np.linspace(0, self.raw_data[0].size-1, self.raw_data[0].size)
                        self.viewer_freq_axis['data'] = np.polyval(calib_coeffs, x_axis_pxls-np.max(x_axis_pxls)/2)
                        self.update_axis()
                    else:
                        self.settings.child('acq_settings', 'spectro_center_freq').hide()
                        self.settings.child('acq_settings', 'spectro_center_freq_txt').show()
                        self.status_center.setStyleSheet("background-color: red")


            elif change == 'parent':
                pass

    @pyqtSlot(list)
    def update_calibration(self, coeffs):
        self.settings.child('calib_settings', 'calib_coeffs', 'center_calib').setValue(coeffs[0])
        self.settings.child('calib_settings', 'calib_coeffs', 'slope_calib').setValue(coeffs[1])
        if len(coeffs) > 2:
            self.settings.child('calib_settings', 'calib_coeffs', 'second_calib').setValue(coeffs[2])
        else:
            self.settings.child('calib_settings', 'calib_coeffs', 'second_calib').setValue(0)
        if len(coeffs) > 3:
            self.settings.child('calib_settings', 'calib_coeffs', 'third_calib').setValue(coeffs[3])
        else:
            self.settings.child('calib_settings', 'calib_coeffs', 'third_calib').setValue(0)


    def set_manual_laser_wl(self, laser_wl):
        messg = QtWidgets.QMessageBox()
        messg.setText('You manually changed the laser wavelength to {:}nm!'.format(laser_wl))
        messg.setInformativeText("Is that correct?")
        messg.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        ret = messg.exec()
        if ret == QtWidgets.QMessageBox.Yes:
            self.status_laser.setText('{:}nm'.format(laser_wl))
            self.status_laser.setStyleSheet("background-color: green")
            self.settings.child('acq_settings', 'units').setOpts(readonly=False)


    def move_laser_wavelength(self, laser_wavelength):
        #do hardware stuff if possible (Mock, labspec...)
        try:
            if self.current_det['laser']:
                self.detector.command_detector.emit(ThreadCommand('set_laser_wl', [laser_wavelength]))
        except Exception as e:
            self.update_status(getLineInfo()+ str(e),self.wait_time,'log')

    @pyqtSlot(OrderedDict)
    def show_data(self,data):
        """
        do stuff with data from the detector if its grab_done_signal has been connected
        Parameters
        ----------
        data: (OrderedDict) #OrderedDict(name=self.title,x_axis=None,y_axis=None,z_axis=None,data0D=None,data1D=None,data2D=None)
        """

        if 'data1D' in data:
            self.raw_data = []
            for key in data['data1D']:
                self.raw_data.append(data['data1D'][key]['data'])
                if 'x_axis' in data['data1D'][key]:
                    x_axis = data['data1D'][key]['x_axis']

                    if np.any(x_axis['data'] != self.viewer_freq_axis['data']) and self.current_det['calib']:
                        self.viewer_freq_axis.update(x_axis)

            self.viewer.show_data(self.raw_data)
            self.update_axis()



    def update_axis(self):
        axis = dict([])
        unit = self.settings.child('acq_settings', 'units').value()
        if unit == 'nm':
            axis['data'] = self.viewer_freq_axis['data']
        elif unit == 'cm-1':
            axis['data'] = Enm2cmrel(self.viewer_freq_axis['data'],
                                     self.settings.child('config_settings', 'laser_wl').value())
        elif unit == 'eV':
            axis['data'] = nm2eV(self.viewer_freq_axis['data'])
        axis['units'] = unit
        axis['label'] = 'Photon energy'
        self.viewer.x_axis = axis









    def create_menu(self, menubar):
        """
        """
        menubar.clear()

        #%% create file menu
        file_menu=menubar.addMenu('File')
        load_action=file_menu.addAction('Load file')
        load_action.triggered.connect(self.load_file)
        save_action=file_menu.addAction('Save file')
        save_action.triggered.connect(self.save_data)

        file_menu.addSeparator()
        quit_action=file_menu.addAction('Quit')
        quit_action.triggered.connect(self.quit_function)

        settings_menu=menubar.addMenu('Settings')
        docked_menu=settings_menu.addMenu('Docked windows')
        action_load=docked_menu.addAction('Load Layout')
        action_save=docked_menu.addAction('Save Layout')


    def load_file(self):
        #init the data browser module
        widg = QtWidgets.QWidget()
        self.data_browser = H5Browser(widg)
        widg.show()

    def quit_function(self):
        #close all stuff that need to be
        self.detector.quit_fun()
        QtWidgets.QApplication.processEvents()
        self.mainwindow.close()

    def create_toolbar(self):
        self.toolbar.addWidget(QtWidgets.QLabel('Acquisition:'))

        iconquit = QtGui.QIcon()
        iconquit.addPixmap(QtGui.QPixmap(":/icons/Icon_Library/close2.png"), QtGui.QIcon.Normal,
                           QtGui.QIcon.Off)
        self.quit_action = QtWidgets.QAction(iconquit, "Quit program", None)
        self.toolbar.addAction(self.quit_action)
        self.quit_action.triggered.connect(self.quit_function)

        iconload = QtGui.QIcon()
        iconload.addPixmap(QtGui.QPixmap(":/icons/Icon_Library/Open.png"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.loadaction = QtWidgets.QAction(iconload, "Load target file (.h5, .png, .jpg) or data from camera", None)
        self.toolbar.addAction(self.loadaction)
        self.loadaction.triggered.connect(self.load_file)


        iconsave = QtGui.QIcon()
        iconsave.addPixmap(QtGui.QPixmap(":/icons/Icon_Library/SaveAs.png"), QtGui.QIcon.Normal,
                           QtGui.QIcon.Off)
        self.saveaction = QtWidgets.QAction(iconsave, "Save current data", None)
        self.toolbar.addAction(self.saveaction)
        self.saveaction.triggered.connect(self.save_data)

        self.det_cb_action = QtWidgets.QComboBox()
        self.det_cb_action.addItems([det['name'] for det in self.detectors])
        self.det_cb_action.currentTextChanged[str].connect(lambda det: self.settings.child('config_settings', 'list_detector').setValue(det))
        self.toolbar.addWidget(self.det_cb_action)

        self.init_action = QtWidgets.QAction("Ini. det", None)
        self.init_action.setCheckable(True)
        self.toolbar.addAction(self.init_action)
        self.init_action.triggered.connect(self.init_detector)

        iconrun = QtGui.QIcon()
        iconrun.addPixmap(QtGui.QPixmap(":/icons/Icon_Library/run2.png"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.grab_action = QtWidgets.QAction(iconrun, 'Grab', None)
        self.grab_action.setCheckable(True)
        self.toolbar.addAction(self.grab_action)
        self.grab_action.triggered.connect(self.grab_detector)

        iconsnap = QtGui.QIcon()
        iconsnap.addPixmap(QtGui.QPixmap(":/icons/Icon_Library/snap.png"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.snap_action = QtWidgets.QAction(iconsnap, 'Snap', None)
        self.snap_action.triggered.connect(self.snap_detector)
        self.toolbar.addAction(self.snap_action)

        self.grab_action.setEnabled(False)
        self.snap_action.setEnabled(False)


    def grab_detector(self):
        self.detector.ui.grab_pb.click()

    def snap_detector(self):
        self.detector.ui.single_pb.click()


    def save_data(self):
        try:
            fname = utils.select_file(start_path=self.save_file_pathname, save=True, ext='h5')

            if not (not (fname)):
                with tables.open_file(str(fname), mode='w', title='an example h5 file name') as h5file:
                    data_to_save = np.squeeze(np.array(self.raw_data))
                    #save metadata
                    h5file.root._v_attrs['settings'] = custom_tree.parameter_to_xml_string(self.settings)
                    for ind in range(data_to_save.shape[1]):
                        arr = h5file.create_array('/', 'data_{:d}'.format(ind), data_to_save[:,ind])
                        arr._v_attrs['shape'] = data_to_save.shape[0]
                        arr._v_attrs['type'] = 'data1D'

                    arr = h5file.create_array('/', 'data_2D', data_to_save)
                    arr._v_attrs['shape'] = data_to_save.shape
                    arr._v_attrs['type'] = 'data2D'



                    logger = "logging"
                    text_atom = tables.atom.ObjectAtom()
                    logger_array = h5file.create_vlarray('/', logger, atom=text_atom)
                    logger_array._v_attrs['type'] = 'list'
                    for ind_log in range(self.logger_list.count()):
                        txt = self.logger_list.item(ind_log).text()
                        logger_array.append(txt)

                st = 'file {:s} has been saved'.format(fname)
                self.add_log(st)
                self.settings.child('min_settings', 'info').setValue(st)


        except Exception as e:
            self.add_log(str(e))


    @pyqtSlot(str)
    def add_log(self, txt):
        """
            Add a log to the logger list from the given text log and the current time

            ================ ========= ======================
            **Parameters**   **Type**   **Description**

             *txt*             string    the log to be added
            ================ ========= ======================

        """
        now = datetime.datetime.now()
        new_item = QtWidgets.QListWidgetItem(str(now) + ": " + txt)
        self.logger_list.addItem(new_item)
        ##to do
        ##self.save_parameters.logger_array.append(str(now)+": "+txt)

    @pyqtSlot(str)
    def emit_log(self, txt):
        """
            Emit a log-signal from the given log index

            =============== ======== =======================
            **Parameters**  **Type** **Description**

             *txt*           string   the log to be emitted
            =============== ======== =======================

        """
        self.log_signal.emit(txt)


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    win = QtWidgets.QMainWindow()
    area = DockArea()
    win.setCentralWidget(area)
    win.resize(1000, 500)
    win.setWindowTitle('pymodaq example')
    prog = Spectrometer(area)
    win.show()
    sys.exit(app.exec_())
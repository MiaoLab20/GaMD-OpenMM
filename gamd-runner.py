"""
Run a GaMD calculation

More here...
"""

import os
import sys
import time
import traceback
import pprint
import argparse
import glob

# TODO: need to remove import * - does not conform to PEP8
from simtk.openmm.app import *
from simtk.openmm import *
from simtk.unit import *

from gamd.langevin.total_boost_integrators import LowerBoundIntegrator
from gamd import utils as utils
from gamd import parser
from gamd import gamdSimulation


def create_output_directories(directories, overwrite_output=False):
    if overwrite_output:
        print("Overwrite output set to True")
        for dir in directories:
            if os.path.exists(dir):
                print("Deleting old output directory:", dir)
                os.system('rm -r %s' % dir)
    
    for dir in directories:
        os.makedirs(dir, 0o755)


def getGlobalVariableNames(integrator):
    for index in range(0, integrator.getNumGlobalVariables()):
        print(integrator.getGlobalVariableName(index))

class Runner:
    def __init__(self, config, gamdSimulation):
        self.config = config
        self.gamdSim = gamdSimulation
    
    def run(self, restart=False):
        initial_temperature = self.config.initial_temperature
        target_temperature = self.config.target_temperature
        output_directory = self.config.output_directory
        restart_checkpoint_frequency = self.config.restart_checkpoint_frequency
        restart_checkpoint_filename = os.path.join(
            output_directory, self.config.restart_checkpoint_filename)
        chunk_size = self.config.chunk_size
        overwrite_output = self.config.overwrite_output
        if not restart:
            create_output_directories(
                [output_directory, 
                 os.path.join(output_directory, "states/"), 
                 os.path.join(output_directory, "positions/"),
                 os.path.join(output_directory, "pdb/"),
                 os.path.join(output_directory, "checkpoints")],
                overwrite_output)
        
        system = self.gamdSim.system
        simulation = self.gamdSim.simulation
        integrator = self.gamdSim.integrator
        #force_groups = self.gamdSim.force_groups
        traj_reporter = self.gamdSim.traj_reporter
        
        # TODO: this whole paragraph needs to be adapted for generic integrator
        group = 1
        for force in system.getForces():
        #     print(force.__class__.__name__)
            if force.__class__.__name__ == 'PeriodicTorsionForce':
                force.setForceGroup(group)
                break
        #     group += 1
        extension = self.config.coordinates_reporter_file_type
        if restart:
            simulation.loadCheckpoint(restart_checkpoint_filename)
            currentStep = int(integrator.getGlobalVariableByName("stepCount"))
            simulation.currentStep = currentStep
            write_mode = "a"
            start_chunk = (currentStep // chunk_size) + 1
            print("restarting from saved checkpoint:", 
                  restart_checkpoint_filename, "at step:", start_chunk)
            # see how many restart files have already been created
            state_data_restart_files_glob = os.path.join(
                output_directory, 'state-data.restart*.log')
            state_data_restarts_list = glob.glob(state_data_restart_files_glob)
            restart_index = len(state_data_restarts_list) + 1
            state_data_name = os.path.join(
                output_directory, 'state-data.restart%d.log' % restart_index)
            traj_name = os.path.join(
                self.config.output_directory, 
                'output.restart%d.%s' % (restart_index, extension))
            
        else:
            simulation.saveState(
                os.path.join(output_directory, "states/initial-state.xml"))
            write_mode = "w"
            start_chunk = 1
            traj_name = os.path.join(
                self.config.output_directory, 'output.%s' % extension)
            state_data_name = os.path.join(output_directory, 'state-data.log')
            
        if traj_reporter:
            simulation.reporters.append(traj_reporter(
                traj_name, self.config.coordinates_reporter_frequency,))
        simulation.reporters.append(utils.ExpandedStateDataReporter(
            system, state_data_name, 
            self.config.energy_reporter_frequency, step=True, 
            brokenOutForceEnergies=True, temperature=True, 
            potentialEnergy=True, totalEnergy=True, 
            volume=True))
            
        
        # TODO: check if we really want to get this quantity from integrator
        # instead of the config object
        end_chunk = int(integrator.get_total_simulation_steps() \
                        // chunk_size) + 1
            
        with open(os.path.join(output_directory, "gamd.log"), write_mode) \
                as gamdLog:
            if not restart:
                gamdLog.write(
                    "# Gaussian accelerated Molecular Dynamics log file\n")
                gamdLog.write(
                    "# All energy terms are stored in unit of kcal/mol\n")
                gamdLog.write(
                    "# ntwx,total_nstep,Unboosted-Potential-Energy,"\
                    "Unboosted-Dihedral-Energy,Total-Force-Weight,"\
                    "Dihedral-Force-Weight,Boost-Energy-Potential,"\
                    "Boost-Energy-Dihedral\n")
            
            for chunk in range(start_chunk, end_chunk):
                if chunk % (restart_checkpoint_frequency // chunk_size) == 0:
                    simulation.saveCheckpoint(restart_checkpoint_filename)
                
                '''
                # TEST
                if chunk == 249:
                    print("sudden, unexpected interruption!")
                    exit()
                # END TEST
                '''
                
                try:
                    simulation.step(chunk_size)
                    state = simulation.context.getState(
                        getEnergy=True, groups={group})
                    
                    # TODO: deal with these strange units issues
                    gamdLog.write("\t".join((
                        str(chunk_size), str(chunk*chunk_size), 
                        str(integrator.get_current_potential_energy()/4.184),
                        str(state.getPotentialEnergy()/(kilojoules_per_mole*4.184)),
                        str(integrator.get_total_force_scaling_factor()),
                        str(integrator.get_dihedral_force_scaling_factor()),
                        str(integrator.get_boost_potential()/4.184),
                        str(integrator.get_dihedral_boost()/4.184) + "\n")))
    
                except Exception as e:
                    print("Failure on step " + str(chunk*chunk_size))
                    print(e)
                    print(integrator.get_current_state())
                    state = simulation.context.getState(
                        getEnergy=True, groups={group})
                    gamdLog.write("\t".join((
                        "Fail Step: " + str(chunk*chunk_size),
                        str(integrator.get_current_potential_energy()/4.184),
                        str(state.getPotentialEnergy()/(4.184*kilojoules_per_mole)),
                        str(integrator.get_total_force_scaling_factor()), 
                        str(integrator.get_dihedral_force_scaling_factor()),
                        str(integrator.get_boost_potential()/4.184),
                        str(integrator.get_dihedral_boost()/4.184) + "\n")))
                    sys.exit(2)
                
                if chunk % (integrator.ntave // chunk_size) == 0:
    
                    simulation.saveState(
                        os.path.join(output_directory, 
                                     "states", str(chunk*chunk_size) + ".xml"))
                    simulation.saveCheckpoint(
                        os.path.join(output_directory, "checkpoints",
                                     str(chunk*chunk_size) + ".bin"))
                    positions_filename = os.path.join(output_directory,
                        "positions", "coordinates-" + str(chunk*chunk_size) \
                        + ".csv")
                    integrator.create_positions_file(positions_filename)
                    
def main():
    argparser = argparse.ArgumentParser(description=__doc__)
    argparser.add_argument(
        'input_file', metavar='INPUT_FILE', type=str, 
        help="name of input file for GaMD calculation. A variety of input"\
        "formats are allowed, but XML format is preferred")
    argparser.add_argument(
        'input_file_type', metavar='INPUT_FILE_TYPE', type=str, 
        help="The type of file being provided. Available options are: 'xml', "\
        "... More to come later")
    argparser.add_argument('-r', '--restart', dest='restart', default=False,
                           help="Restart simulation from backup checkpoint in "\
                           "input file", action="store_true")
    
    args = argparser.parse_args() # parse the args into a dictionary
    args = vars(args)
    input_filename = args['input_file']
    input_file_type = args['input_file_type']
    restart = args['restart']
    
    parserFactory = parser.ParserFactory()
    config = parserFactory.parse_file(input_filename, input_file_type)
    gamdSimulationFactory = gamdSimulation.GamdSimulationFactory()
    gamdSim = gamdSimulationFactory.createGamdSimulation(config)
    
    # If desired, modify OpenMM objects in gamdSimulation object here...
    
    runner = Runner(config, gamdSim)
    runner.run(restart)
    
               
if __name__ == "__main__":
    main()
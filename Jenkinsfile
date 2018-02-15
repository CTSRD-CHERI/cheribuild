pipeline {
  agent {
    node {
      label 'docker'
    }
    
  }
  stages {
    stage('Test Python 3.5.0') {
      agent {
        docker {
          reuseNode true
          image 'python:3.5.0'
          args '-u 0'
        }
        
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          sh '''
set -e
env | sort
./cheribuild.py -p __run_everything__ --cheribsd/crossbuild
pip install pytest
pytest -v --junit-xml 3.5.0-results.xml tests || echo "Some tests failed"
targets=$(./cheribuild.py --list-targets | grep -v Available)
echo "targets=$targets"
for i in $targets; do
  WORKSPACE=/tmp ./jenkins-cheri-build.py --cpu=cheri128 -p $i > /dev/null;
done
'''
        }
        
        junit '3.5.0-results.xml'
      }
    }
    stage('Test Python 3.6') {
      agent {
        docker {
          reuseNode true
          image 'python:3.6'
          args '-u 0'
        }
        
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          sh '''
set -e
env | sort
./cheribuild.py -p __run_everything__ --cheribsd/crossbuild
pip install pytest
pytest -v --junit-xml 3.6-results.xml tests || echo "Some tests failed"
targets=$(./cheribuild.py --list-targets | grep -v Available)
echo "targets=$targets"
for i in $targets; do
  WORKSPACE=/tmp ./jenkins-cheri-build.py --cpu=cheri128 -p $i > /dev/null;
done
'''
        }
        
        junit '3.6-results.xml'
      }
    }
    stage('Test Python RC') {
      agent {
        docker {
          reuseNode true
          image 'python:rc'
          args '-u 0'
        }
        
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          sh '''
set -e
env | sort
./cheribuild.py -p __run_everything__ --cheribsd/crossbuild
pip install pytest
pytest -v --junit-xml python-rc-results.xml tests || echo "Some tests failed"
targets=$(./cheribuild.py --list-targets | grep -v Available)
echo "targets=$targets"
for i in $targets; do
  WORKSPACE=/tmp ./jenkins-cheri-build.py --cpu=cheri128 -p $i > /dev/null;
done
'''
        }
        
        junit 'python-rc-results.xml'
      }
    }
    stage('Test Ubuntu 16.04') {
      agent {
        dockerfile {
          filename 'tests/ubuntu.Dockerfile'
        }
        
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          sh '''
set -e
env | sort
./cheribuild.py -p __run_everything__ --cheribsd/crossbuild
# pip3 install pytest
py.test-3 -v --junit-xml ubuntu-results.xml tests || echo "Some tests failed"
targets=$(./cheribuild.py --list-targets | grep -v Available)
echo "targets=$targets"
for i in $targets; do
  WORKSPACE=/tmp ./jenkins-cheri-build.py --cpu=cheri128 -p $i > /dev/null;
done
'''
        }
        
        junit 'ubuntu-results.xml'
      }
    }
  }
  environment {
    PYTHONDONTWRITEBYTECODE = '1'
  }
  post {
    failure {
      mail(to: 'alr48@cl.cam.ac.uk', subject: "Failed Pipeline: ${currentBuild.fullDisplayName}", body: "Something is wrong with ${env.BUILD_URL}")
      
    }
    
  }
  options {
    timestamps()
  }
}
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
        }
        
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          sh 'env | sort && ./cheribuild.py -p __run_everything__ --cheribsd/crossbuild'
        }
        
      }
    }
    stage('Test Python 3.6') {
      agent {
        docker {
          reuseNode true
          image 'python:3.6'
        }
        
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          sh 'env | sort && ./cheribuild.py -p __run_everything__ --cheribsd/crossbuild'
        }
        
      }
    }
    stage('Test Python RC') {
      agent {
        docker {
          reuseNode true
          image 'python:rc'
        }
        
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          sh 'env | sort && ./cheribuild.py -p __run_everything__ --cheribsd/crossbuild'
        }
        
      }
    }
    stage('Test Ubuntu 16.04') {
      agent {
        dockerfile {
          filename 'test/ubuntu.Dockerfile'
        }
        
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          sh 'env | sort && ./cheribuild.py -p __run_everything__ --cheribsd/crossbuild'
        }
        
      }
    }
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
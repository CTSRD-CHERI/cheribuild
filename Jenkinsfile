pipeline {
  agent {
    node {
      label 'docker'
    }
    
  }
  stages {
    stage('Test Python 3.5') {
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
  }
  post {
    failure {
      mail(to: 'alr48@cl.cam.acuk', subject: "Failed Pipeline: ${currentBuild.fullDisplayName}", body: "Something is wrong with ${env.BUILD_URL}")
      
    }
    
  }
  options {
    timestamps()
  }
}